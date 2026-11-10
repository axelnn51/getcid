const Database = require('better-sqlite3');
const path = require('path');

const DB_PATH = path.join(__dirname, 'data', 'getcid.db');

// Asegurar que el directorio data exista
const fs = require('fs');
if (!fs.existsSync(path.join(__dirname, 'data'))) {
    fs.mkdirSync(path.join(__dirname, 'data'), { recursive: true });
}

const db = new Database(DB_PATH);

// WAL mode para mejor rendimiento
db.pragma('journal_mode = WAL');

// ============================================================
// CREAR TABLAS
// ============================================================
db.exec(`
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        telegram_id TEXT UNIQUE,
        telegram_username TEXT,
        balance INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        source TEXT DEFAULT 'web',
        iid TEXT,
        cid TEXT,
        status TEXT,
        time_ms INTEGER,
        strategy TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS credits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        type TEXT,
        reason TEXT,
        admin_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    -- NUEVO: Créditos por pedido (1 licencia = 1 crédito)
    CREATE TABLE IF NOT EXISTS order_credits (
        order_id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        total_credits INTEGER NOT NULL DEFAULT 0,
        used_credits INTEGER NOT NULL DEFAULT 0,
        synced_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    -- Mantener tabla legacy para compatibilidad
    CREATE TABLE IF NOT EXISTS used_orders (
        order_id TEXT PRIMARY KEY,
        claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    -- Presets de productos rápidos para bot (/pedido21, /pedido24, etc.)
    CREATE TABLE IF NOT EXISTS product_presets (
        code TEXT PRIMARY KEY,
        product_name TEXT NOT NULL,
        default_credits INTEGER NOT NULL DEFAULT 1,
        guide_url TEXT DEFAULT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    -- Cola FIFO local de keys por producto (cada ENTER = 1 fila, permite repetidas o únicas)
    CREATE TABLE IF NOT EXISTS key_stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_code TEXT NOT NULL,
        license_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'available',
        assigned_order_id TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        used_at DATETIME
    );

    CREATE INDEX IF NOT EXISTS idx_key_stock_lookup ON key_stock(product_code, status, id);
`);

// Migración segura para BD existente sin columna guide_url
const presetCols = db.prepare('PRAGMA table_info(product_presets)').all().map(c => c.name);
if (!presetCols.includes('guide_url')) {
    db.exec('ALTER TABLE product_presets ADD COLUMN guide_url TEXT DEFAULT NULL');
}

db.prepare(`
    INSERT OR IGNORE INTO product_presets (code, product_name, default_credits, guide_url)
    VALUES
        ('21', 'Office Pro Plus 2021', 1, 'https://cdkeysperu.com/guias/office/office-2021'),
        ('24', 'Office Pro Plus LTSC 2024', 1, 'https://cdkeysperu.com/guias/office/office-2024')
`).run();

// Rellenar guide_url si los presets 21/24 ya existían de la versión anterior
db.prepare("UPDATE product_presets SET guide_url = 'https://cdkeysperu.com/guias/office/office-2021' WHERE code = '21' AND guide_url IS NULL").run();
db.prepare("UPDATE product_presets SET guide_url = 'https://cdkeysperu.com/guias/office/office-2024' WHERE code = '24' AND guide_url IS NULL").run();

// ============================================================
// FUNCIONES DE USUARIO
// ============================================================

function findUserByEmail(email) {
    return db.prepare('SELECT * FROM users WHERE email = ?').get(email?.toLowerCase());
}

function findUserByTelegram(telegramId) {
    return db.prepare('SELECT * FROM users WHERE telegram_id = ?').get(String(telegramId));
}

function createUser({ email, telegram_id, telegram_username }) {
    const stmt = db.prepare(`
        INSERT OR IGNORE INTO users (email, telegram_id, telegram_username)
        VALUES (?, ?, ?)
    `);
    stmt.run(email?.toLowerCase() || null, telegram_id ? String(telegram_id) : null, telegram_username || null);
    return findUserByEmail(email) || findUserByTelegram(telegram_id);
}

function setAdmin(userId) {
    db.prepare('UPDATE users SET is_admin = 1 WHERE id = ?').run(userId);
}

// ============================================================
// FUNCIONES DE CRÉDITOS (Telegram - balance manual por admin)
// ============================================================

function getBalance(userId) {
    const row = db.prepare('SELECT balance FROM users WHERE id = ?').get(userId);
    return row ? row.balance : 0;
}

function addCredits(userId, amount, reason = 'manual', adminId = null) {
    const txn = db.transaction(() => {
        db.prepare('UPDATE users SET balance = balance + ? WHERE id = ?').run(amount, userId);
        db.prepare(`
            INSERT INTO credits (user_id, amount, type, reason, admin_id) VALUES (?, ?, 'add', ?, ?)
        `).run(userId, amount, reason, adminId);
    });
    txn();
    return getBalance(userId);
}

function debitCredit(userId) {
    const user = db.prepare('SELECT balance FROM users WHERE id = ?').get(userId);
    if (!user || user.balance <= 0) return false;

    db.prepare('UPDATE users SET balance = balance - 1 WHERE id = ?').run(userId);
    db.prepare(`
        INSERT INTO credits (user_id, amount, type, reason) VALUES (?, -1, 'debit', 'getcid')
    `).run(userId);
    return true;
}

// ============================================================
// NUEVO: CRÉDITOS POR PEDIDO (Web - basado en WooCommerce)
// ============================================================

/**
 * Sincroniza un pedido de WooCommerce en la tabla order_credits.
 * Si ya existe, no lo sobrescribe.
 * @param {string} orderId - ID del pedido
 * @param {string} email - Email de facturación
 * @param {number} totalLicenses - Cantidad total de licencias en el pedido
 */
function syncOrderCredits(orderId, email, totalLicenses) {
    const existing = db.prepare('SELECT * FROM order_credits WHERE order_id = ?').get(String(orderId));
    if (!existing) {
        db.prepare(`
            INSERT INTO order_credits (order_id, email, total_credits, used_credits)
            VALUES (?, ?, ?, 0)
        `).run(String(orderId), email.toLowerCase(), totalLicenses);
    }
}

/**
 * Obtiene los créditos restantes de un pedido específico.
 * Busca tanto por ID exacto, insensible a mayúsculas, o con prefijo 'CDK-'.
 * @returns {{ total: number, used: number, remaining: number, orderId: string, email: string } | null}
 */
function getOrderBalance(orderId) {
    if (!orderId) return null;
    let str = String(orderId).trim().replace(/^#+/, '').trim();
    const upper = str.toUpperCase();
    const prefixed = upper.startsWith('CDK-') ? upper : `CDK-${upper}`;
    const digitsOnly = upper.replace(/\D/g, '');
    const numPrefixed = digitsOnly ? `CDK-${digitsOnly}` : upper;

    const row = db.prepare(`
        SELECT * FROM order_credits 
        WHERE order_id = ? 
           OR UPPER(order_id) = ? 
           OR order_id = ?
           OR order_id = ?
        LIMIT 1
    `).get(str, upper, prefixed, numPrefixed);

    if (!row) return null;
    return {
        orderId: row.order_id,
        total: row.total_credits,
        used: row.used_credits,
        remaining: row.total_credits - row.used_credits,
        email: row.email
    };
}

/**
 * Obtiene el saldo global de un email (suma de créditos restantes de todos sus pedidos).
 * @returns {number}
 */
function getEmailBalance(email) {
    const row = db.prepare(`
        SELECT COALESCE(SUM(total_credits - used_credits), 0) as remaining
        FROM order_credits WHERE email = ?
    `).get(email.toLowerCase());
    return row ? row.remaining : 0;
}

/**
 * Consume 1 crédito de un pedido específico.
 * @returns {boolean} true si se pudo consumir
 */
function consumeOrderCredit(orderId) {
    if (!orderId) return false;
    let str = String(orderId).trim().replace(/^#+/, '').trim();
    const upper = str.toUpperCase();
    const prefixed = upper.startsWith('CDK-') ? upper : `CDK-${upper}`;
    const digitsOnly = upper.replace(/\D/g, '');
    const numPrefixed = digitsOnly ? `CDK-${digitsOnly}` : upper;

    const order = db.prepare(`
        SELECT * FROM order_credits 
        WHERE order_id = ? 
           OR UPPER(order_id) = ? 
           OR order_id = ?
           OR order_id = ?
        LIMIT 1
    `).get(str, upper, prefixed, numPrefixed);

    if (!order || order.used_credits >= order.total_credits) return false;
    
    db.prepare('UPDATE order_credits SET used_credits = used_credits + 1 WHERE order_id = ?')
        .run(order.order_id);
    return true;
}

/**
 * Consume 1 crédito del pedido más antiguo con saldo disponible para un email.
 * @returns {boolean} true si se pudo consumir
 */
function consumeEmailCredit(email) {
    const order = db.prepare(`
        SELECT * FROM order_credits 
        WHERE email = ? AND used_credits < total_credits
        ORDER BY synced_at ASC
        LIMIT 1
    `).get(email.toLowerCase());
    
    if (!order) return false;
    
    db.prepare('UPDATE order_credits SET used_credits = used_credits + 1 WHERE order_id = ?')
        .run(order.order_id);
    return true;
}

/**
 * Obtiene todos los pedidos de un email con sus saldos.
 */
function getOrdersByEmail(email) {
    return db.prepare(`
        SELECT order_id, total_credits, used_credits, 
               (total_credits - used_credits) as remaining,
               synced_at
        FROM order_credits WHERE email = ?
        ORDER BY synced_at DESC
    `).all(email.toLowerCase());
}

/**
 * Crea o asigna un pedido manual con cierta cantidad de créditos.
 * Utiliza por defecto el prefijo oficial 'CDK-XXXXX' (ej. CDK-84920).
 * Esto garantiza que NUNCA colisione con los IDs puramente numéricos de WooCommerce (ej. 15632).
 */
function createManualOrder(credits = 1, customOrderId = null, email = 'manual@cdkeysperu.com') {
    let orderId = customOrderId ? String(customOrderId).trim().toUpperCase() : null;
    if (!orderId) {
        let attempts = 0;
        do {
            const randomNum = Math.floor(10000 + Math.random() * 90000);
            orderId = `CDK-${randomNum}`;
            attempts++;
            if (attempts > 100) {
                orderId = `CDK-${Date.now().toString().slice(-6)}`;
                break;
            }
        } while (db.prepare('SELECT order_id FROM order_credits WHERE UPPER(order_id) = ?').get(orderId));
    }

    const numCredits = Math.max(1, parseInt(credits, 10) || 1);
    const userEmail = (email || 'manual@cdkeysperu.com').toLowerCase();

    db.prepare(`
        INSERT OR REPLACE INTO order_credits (order_id, email, total_credits, used_credits, synced_at)
        VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
    `).run(orderId, userEmail, numCredits);

    let user = findUserByEmail(userEmail);
    if (!user) user = createUser({ email: userEmail });

    return {
        orderId,
        email: userEmail,
        totalCredits: numCredits,
        remaining: numCredits
    };
}

function getManualOrders(limit = 10) {
    return db.prepare(`
        SELECT order_id, email, total_credits, used_credits, (total_credits - used_credits) as remaining, synced_at
        FROM order_credits
        ORDER BY synced_at DESC
        LIMIT ?
    `).all(limit);
}

// ============================================================
// FUNCIONES DE TRANSACCIONES
// ============================================================

function logTransaction(userId, source, iid, cid, status, timeMs, strategy) {
    const safeCid = typeof cid === 'string' 
        ? cid 
        : (cid ? (cid.cid || JSON.stringify(cid)) : null);
    db.prepare(`
        INSERT INTO transactions (user_id, source, iid, cid, status, time_ms, strategy)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(userId, source, iid, safeCid, status, timeMs || 0, strategy || null);
}

function getTransactions(userId, limit = 50) {
    return db.prepare('SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?').all(userId, limit);
}

function getAllUsers() {
    return db.prepare('SELECT id, email, telegram_id, telegram_username, balance, is_admin, created_at FROM users ORDER BY created_at DESC').all();
}

function getAllTransactions(limit = 100) {
    return db.prepare(`
        SELECT t.*, u.email, u.telegram_username 
        FROM transactions t 
        LEFT JOIN users u ON t.user_id = u.id 
        ORDER BY t.created_at DESC LIMIT ?
    `).all(limit);
}

function getStats() {
    const total = db.prepare('SELECT COUNT(*) as c FROM transactions WHERE status = ?').get('success');
    const today = db.prepare(`SELECT COUNT(*) as c FROM transactions WHERE status = ? AND date(created_at) = date('now')`).get('success');
    const users = db.prepare('SELECT COUNT(*) as c FROM users').get();
    return { totalCids: total.c, todayCids: today.c, totalUsers: users.c };
}

// Legacy - mantener compatibilidad
function isOrderUsed(orderId) {
    const row = db.prepare('SELECT order_id FROM used_orders WHERE order_id = ?').get(String(orderId));
    return !!row;
}
function markOrderUsed(orderId) {
    db.prepare('INSERT OR IGNORE INTO used_orders (order_id) VALUES (?)').run(String(orderId));
}

// ============================================================
// PRESETS DE PRODUCTOS Y STOCK LOCAL DE KEYS (FIFO)
// ============================================================

function normalizeProductCode(code) {
    return String(code || '').trim().toLowerCase().replace(/^\/?(pedido|cargar|stock|guia)/i, '');
}

function getProductPreset(code) {
    const clean = normalizeProductCode(code);
    if (!clean) return null;
    return db.prepare('SELECT code, product_name, default_credits, guide_url FROM product_presets WHERE code = ?').get(clean) || null;
}

function getAllProductPresetsWithStock() {
    return db.prepare(`
        SELECT p.code, p.product_name, p.default_credits, p.guide_url,
               COALESCE(SUM(CASE WHEN k.status = 'available' THEN 1 ELSE 0 END), 0) AS available_count,
               COALESCE(SUM(CASE WHEN k.status = 'used' THEN 1 ELSE 0 END), 0) AS used_count
        FROM product_presets p
        LEFT JOIN key_stock k ON k.product_code = p.code
        GROUP BY p.code, p.product_name, p.default_credits, p.guide_url
        ORDER BY p.code ASC
    `).all();
}

function upsertProductPreset(code, productName, defaultCredits = 1, guideUrl = null) {
    const clean = normalizeProductCode(code);
    if (!clean || !/^[a-z0-9_-]+$/i.test(clean) || clean === 's') {
        throw new Error('Código de producto inválido (usa ej. 21, 24, 11, win11).');
    }
    const name = String(productName || '').trim();
    if (!name) throw new Error('Nombre del producto vacío.');
    const credits = Math.max(1, parseInt(defaultCredits, 10) || 1);
    const cleanGuide = guideUrl ? String(guideUrl).trim() : null;

    db.prepare(`
        INSERT INTO product_presets (code, product_name, default_credits, guide_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            product_name = excluded.product_name,
            default_credits = excluded.default_credits,
            guide_url = COALESCE(excluded.guide_url, product_presets.guide_url)
    `).run(clean, name, credits, cleanGuide);

    return getProductPreset(clean);
}

function deleteProductPreset(code) {
    const clean = normalizeProductCode(code);
    const info = db.prepare('DELETE FROM product_presets WHERE code = ?').run(clean);
    return info.changes > 0;
}

function countAvailableStock(productCode) {
    const clean = normalizeProductCode(productCode);
    const row = db.prepare(`SELECT COUNT(*) as c FROM key_stock WHERE product_code = ? AND status = 'available'`).get(clean);
    return row ? row.c : 0;
}

/**
 * Agrega una lista de keys al stock FIFO respetando el orden exacto del arreglo (cada ENTER = 1 fila).
 */
function addKeysToStock(productCode, rawKeys) {
    const clean = normalizeProductCode(productCode);
    const keys = (Array.isArray(rawKeys) ? rawKeys : String(rawKeys || '').split(/\r?\n/))
        .map(k => k.trim())
        .filter(Boolean);

    if (keys.length === 0) return { added: 0, remaining: countAvailableStock(clean) };

    const insertStmt = db.prepare(`
        INSERT INTO key_stock (product_code, license_key, status)
        VALUES (?, ?, 'available')
    `);

    const insertMany = db.transaction((items) => {
        for (const k of items) {
            insertStmt.run(clean, k);
        }
    });
    insertMany(keys);

    return { added: keys.length, remaining: countAvailableStock(clean) };
}

/**
 * Extrae atómicamente hasta `count` keys en estricto orden FIFO (ORDER BY id ASC).
 * Si hay menos keys que `count`, entrega las disponibles sin fallar.
 */
function popKeysFromStock(productCode, count = 1, orderId = null) {
    const clean = normalizeProductCode(productCode);
    const qty = Math.max(1, parseInt(count, 10) || 1);

    const txn = db.transaction(() => {
        const rows = db.prepare(`
            SELECT id, license_key FROM key_stock
            WHERE product_code = ? AND status = 'available'
            ORDER BY id ASC
            LIMIT ?
        `).all(clean, qty);

        if (rows.length > 0) {
            const updateStmt = db.prepare(`
                UPDATE key_stock
                SET status = 'used', assigned_order_id = ?, used_at = CURRENT_TIMESTAMP
                WHERE id = ?
            `);
            for (const row of rows) {
                updateStmt.run(orderId || null, row.id);
            }
        }

        const remaining = countAvailableStock(clean);
        return {
            keys: rows.map(r => r.license_key),
            remaining
        };
    });

    return txn();
}

function getStockQueue(productCode, limit = 15) {
    const clean = normalizeProductCode(productCode);
    const available = countAvailableStock(clean);
    const usedRow = db.prepare(`SELECT COUNT(*) as c FROM key_stock WHERE product_code = ? AND status = 'used'`).get(clean);
    const nextKeys = db.prepare(`
        SELECT id, license_key FROM key_stock
        WHERE product_code = ? AND status = 'available'
        ORDER BY id ASC
        LIMIT ?
    `).all(clean, limit);

    return {
        available,
        used: usedRow ? usedRow.c : 0,
        nextKeys: nextKeys.map(r => r.license_key)
    };
}

function clearAvailableStock(productCode) {
    const clean = normalizeProductCode(productCode);
    const info = db.prepare(`DELETE FROM key_stock WHERE product_code = ? AND status = 'available'`).run(clean);
    return info.changes;
}

module.exports = {
    findUserByEmail, findUserByTelegram, createUser, setAdmin,
    getBalance, addCredits, debitCredit,
    // Nuevo sistema de créditos por pedido
    syncOrderCredits, getOrderBalance, getEmailBalance,
    consumeOrderCredit, consumeEmailCredit, getOrdersByEmail,
    createManualOrder, getManualOrders,
    // Transacciones
    logTransaction, getTransactions,
    getAllUsers, getAllTransactions, getStats,
    isOrderUsed, markOrderUsed,
    // Presets y stock local FIFO
    getProductPreset, getAllProductPresetsWithStock,
    upsertProductPreset, deleteProductPreset,
    addKeysToStock, popKeysFromStock, getStockQueue, clearAvailableStock
};
