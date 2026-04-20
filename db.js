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

    CREATE TABLE IF NOT EXISTS used_orders (
        order_id TEXT PRIMARY KEY,
        claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
`);

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
// FUNCIONES DE CRÉDITOS
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
// FUNCIONES DE TRANSACCIONES
// ============================================================

function logTransaction(userId, source, iid, cid, status, timeMs, strategy) {
    db.prepare(`
        INSERT INTO transactions (user_id, source, iid, cid, status, time_ms, strategy)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(userId, source, iid, cid || null, status, timeMs || 0, strategy || null);
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

function isOrderUsed(orderId) {
    const row = db.prepare('SELECT order_id FROM used_orders WHERE order_id = ?').get(String(orderId));
    return !!row;
}
function markOrderUsed(orderId) {
    db.prepare('INSERT OR IGNORE INTO used_orders (order_id) VALUES (?)').run(String(orderId));
}

module.exports = {
    findUserByEmail, findUserByTelegram, createUser, setAdmin,
    getBalance, addCredits, debitCredit,
    logTransaction, getTransactions,
    getAllUsers, getAllTransactions, getStats,
    isOrderUsed, markOrderUsed
};
