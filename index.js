// dotenv es opcional (en Docker las vars vienen del entorno)
try { require('dotenv').config(); } catch(e) {}
const express = require('express');
const cors = require('cors');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const rateLimit = require('express-rate-limit');
const { getConfirmationID, CIDError } = require('./cid_helper');
const db = require('./db');
const wc = require('./woocommerce');
const { initWorker, ocrAndGetCID } = require('./ocr');

const app = express();
const PORT = process.env.PORT || 3000;
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || 'admin123';

app.use(cors());
app.use(express.static(path.join(__dirname, 'public')));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

if (!fs.existsSync(path.join(__dirname, 'uploads'))) fs.mkdirSync('uploads');
const upload = multer({ dest: 'uploads/' });

// ============================================================
// Utilidades de Error
// ============================================================
function formatIIDForDisplay(iid) {
    if (!iid) return null;
    return iid.match(/.{1,7}/g)?.join(' ') || iid;
}

function buildErrorResponse(error, detectedIID) {
    const iid = error?.iid || detectedIID || null;
    const iidDisplay = formatIIDForDisplay(iid);

    // CIDError con mensaje descriptivo
    if (error instanceof CIDError || (error && error.code)) {
        let msg = error.userMessage || error.message;
        if (iidDisplay) msg += `\n\n📝 IID detectado:\n${iidDisplay}`;
        return { success: false, error: msg, errorCode: error.code, iid: iid };
    }

    // Error genérico
    let msg = 'Error procesando solicitud.';
    if (iidDisplay) msg += `\n\n📝 IID detectado:\n${iidDisplay}`;
    return { success: false, error: msg, errorCode: 'UNKNOWN', iid: iid };
}

// ============================================================
// Limpieza periódica de uploads (cada 30 min)
// ============================================================
setInterval(() => {
    const uploadsDir = path.join(__dirname, 'uploads');
    if (!fs.existsSync(uploadsDir)) return;
    const now = Date.now();
    try {
        for (const file of fs.readdirSync(uploadsDir)) {
            const filePath = path.join(uploadsDir, file);
            const stat = fs.statSync(filePath);
            // Borrar archivos de más de 10 minutos
            if (now - stat.mtimeMs > 10 * 60 * 1000) {
                fs.unlinkSync(filePath);
            }
        }
    } catch (e) { /* ignore cleanup errors */ }
}, 30 * 60 * 1000);

// ============================================================
// Health Check
// ============================================================
app.get('/api/health', (req, res) => {
    res.json({ 
        status: 'ok', 
        uptime: Math.floor(process.uptime()),
        timestamp: new Date().toISOString(),
        woocommerce: wc.isConfigured() ? 'configured' : 'not_configured',
        bot: process.env.BOT_TOKEN ? 'configured' : 'not_configured'
    });
});

// ============================================================
// API: OCR rápido local (sin créditos)
// ============================================================
app.post('/api/process-image', upload.single('image'), async (req, res) => {
    if (!req.file) return res.status(400).json({ success: false, error: 'No image' });
    try {
        const result = await ocrAndGetCID(req.file.path, getConfirmationID);
        if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        if (result.success) return res.json(result);
        return res.json({ 
            success: false, 
            error: 'No se pudo extraer el IID o checksum inválido.',
            detectedDigits: result.detectedDigits || null 
        });
    } catch (e) {
        if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        if (e instanceof CIDError) return res.status(400).json(buildErrorResponse(e));
        res.status(500).json(buildErrorResponse(e));
    }
});

// ============================================================
// API: Solo OCR (extrae IID sin pedir CID)
// ============================================================
app.post('/api/ocr-only', upload.single('image'), async (req, res) => {
    if (!req.file) return res.status(400).json({ success: false, error: 'No image' });
    try {
        const { ocrExtractOnly } = require('./ocr');
        const result = await ocrExtractOnly(req.file.path);
        if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        return res.json(result);
    } catch (e) {
        if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        res.status(500).json({ success: false, error: e.message });
    }
});

const apiLimiter = rateLimit({
    windowMs: 30 * 60 * 1000, 
    max: 5, 
    skipSuccessfulRequests: true,
    message: { success: false, error: 'Demasiados intentos fallidos desde esta IP. Por favor espera 30 minutos antes de volver a intentar.' }
});

// ============================================================
// SISTEMA DE TOKENS CORREGIDO
// ============================================================
// Regla: 1 pedido completado = 1 token, NUNCA se duplica
// - Por número de pedido: solo acredita ese pedido específico
// - Por email: sincroniza todos los pedidos NO usados de ese email
// - NUNCA hace ambas cosas en cascada
// ============================================================
async function syncAndGetUser(identifier) {
    const isOrderNumber = /^\d+$/.test(identifier);

    if (isOrderNumber && wc.isConfigured()) {
        // ===== MODO PEDIDO: Solo acreditar este pedido específico =====
        const order = await wc.getOrderById(identifier);
        
        if (!order) return null; // Pedido no existe
        if (order.status !== 'completed') return null; // No completado

        const resolvedEmail = order.billing?.email?.toLowerCase();
        if (!resolvedEmail) return null;

        // Crear usuario si no existe
        let user = db.findUserByEmail(resolvedEmail);
        if (!user) user = db.createUser({ email: resolvedEmail });

        // Acreditar SOLO este pedido si no fue usado
        if (!db.isOrderUsed(identifier)) {
            db.markOrderUsed(identifier);
            db.addCredits(user.id, 1, 'woocommerce_order_' + identifier);
        }

        // Retornar usuario actualizado — NO consultar más pedidos por email
        return db.findUserByEmail(resolvedEmail);
    }

    // ===== MODO EMAIL: Sincronizar todos los pedidos del email =====
    if (identifier.includes('@') && wc.isConfigured()) {
        const orders = await wc.getOrdersByEmail(identifier);
        
        let user = db.findUserByEmail(identifier);

        if (orders && orders.length > 0) {
            if (!user) user = db.createUser({ email: identifier });
            
            for (const order of orders) {
                if (order.status === 'completed' && !db.isOrderUsed(order.id)) {
                    db.markOrderUsed(order.id);
                    db.addCredits(user.id, 1, 'woocommerce_order_' + order.id);
                }
            }
        }

        return user ? db.findUserByEmail(identifier) : null;
    }

    // Sin WooCommerce o formato no reconocido
    return db.findUserByEmail(identifier);
}

// ============================================================
// API: Portal Web con créditos (por email o num de pedido)
// ============================================================
app.post('/api/portal/getcid', apiLimiter, upload.single('screenshot'), async (req, res) => {
    const { email, iid } = req.body;
    if (!email) return res.status(400).json({ success: false, error: 'Email o Nro de pedido requerido.' });

    const identifier = email.trim().toLowerCase();
    const user = await syncAndGetUser(identifier);

    if (!user) return res.status(400).json({ success: false, error: 'No encontrado. Verifica que sea tu email de compra o número de pedido y que el pago esté Completado.' });
    if (user.balance <= 0) return res.status(400).json({ success: false, error: 'Sin créditos. Contacta soporte o realiza una nueva compra en cdkeysperu.com.' });

    const startTime = Date.now();
    try {
        let cidResult;
        if (req.file) {
            cidResult = await ocrAndGetCID(req.file.path, getConfirmationID);
            if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        } else if (iid) {
            const cleanIid = iid.replace(/\D/g, '');
            const cid = await getConfirmationID(cleanIid);
            cidResult = { success: true, iid: cleanIid, cid, strategy: 'direct', method: 'manual' };
        } else {
            return res.status(400).json({ success: false, error: 'Envía un IID o una imagen.' });
        }

        const elapsed = Date.now() - startTime;
        if (cidResult.success) {
            db.debitCredit(user.id);
            const newBalance = db.getBalance(user.id);
            const cidStr = Array.isArray(cidResult.cid) ? cidResult.cid.join('-') : cidResult.cid;
            db.logTransaction(user.id, 'web', cidResult.iid, cidStr, 'success', elapsed, cidResult.strategy);
            return res.json({ success: true, iid: cidResult.iid, cid: cidResult.cid, balance: newBalance, time_ms: elapsed });
        } else {
            // OCR falló completamente
            const detectedIID = cidResult.detectedDigits || null;
            db.logTransaction(user.id, 'web', detectedIID, null, 'failed', elapsed, null);
            
            let errorMsg = '❌ No se pudo detectar el IID en la imagen.';
            if (detectedIID) {
                errorMsg += `\n\n📝 Dígitos detectados (${detectedIID.length}):\n${formatIIDForDisplay(detectedIID)}`;
                errorMsg += '\n\nVerifica que la imagen sea nítida o escribe el IID manualmente.';
            } else {
                errorMsg += '\nIntenta con una foto más nítida o escribe el IID manualmente.';
            }
            return res.status(400).json({ success: false, error: errorMsg });
        }
    } catch (e) {
        if (req.file && fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        const elapsed = Date.now() - startTime;
        const iidForLog = e?.iid || iid?.replace(/\D/g, '') || null;
        db.logTransaction(user.id, 'web', iidForLog, null, e?.code || 'error', elapsed, null);
        return res.status(400).json(buildErrorResponse(e, iidForLog));
    }
});

// ============================================================
// API: Verificar balance por email o pedido
// ============================================================
app.get('/api/check-balance', apiLimiter, async (req, res) => {
    const identifier = (req.query.email || '').trim().toLowerCase();
    if (!identifier) return res.json({ found: false });

    const user = await syncAndGetUser(identifier);
    if (!user) return res.json({ found: false });
    return res.json({ found: true, balance: user.balance });
});

// ============================================================
// ADMIN API
// ============================================================
function authAdmin(req, res, next) {
    const pw = req.headers['x-admin-password'] || req.query.pw;
    if (pw !== ADMIN_PASSWORD) return res.status(401).json({ error: 'No autorizado' });
    next();
}

app.get('/api/admin/users', authAdmin, (req, res) => res.json(db.getAllUsers()));
app.get('/api/admin/transactions', authAdmin, (req, res) => res.json(db.getAllTransactions(200)));
app.get('/api/admin/stats', authAdmin, (req, res) => res.json(db.getStats()));

app.post('/api/admin/add-user', authAdmin, (req, res) => {
    const { email, credits } = req.body;
    if (!email) return res.json({ error: 'Email requerido' });
    let user = db.findUserByEmail(email);
    if (!user) user = db.createUser({ email });
    if (credits > 0) db.addCredits(user.id, parseInt(credits), 'admin_web');
    res.json({ success: true, user: db.findUserByEmail(email) });
});

app.post('/api/admin/add-credits', authAdmin, (req, res) => {
    const { user_id, amount } = req.body;
    if (!user_id || !amount) return res.json({ error: 'user_id y amount requeridos' });
    const newBalance = db.addCredits(parseInt(user_id), parseInt(amount), 'admin_web');
    res.json({ success: true, balance: newBalance });
});

// ============================================================
// STARTUP
// ============================================================
process.on('uncaughtException', (err) => console.error('[UNCAUGHT]', err));
process.on('unhandledRejection', (err) => console.error('[UNHANDLED]', err));

// Pre-cargar OCR antes de aceptar peticiones
initWorker().then(() => {
    app.listen(PORT, () => {
        console.log(`🌐 Web server en http://localhost:${PORT}`);
        console.log(`📋 Variables detectadas:`);
        console.log(`   BOT_TOKEN: ${process.env.BOT_TOKEN ? '✓ (' + process.env.BOT_TOKEN.substring(0, 10) + '...)' : '✗ NO ENCONTRADO'}`);
        console.log(`   ADMIN_IDS: ${process.env.ADMIN_IDS || '✗'}`);
        console.log(`   WC_URL: ${process.env.WC_URL || '✗'}`);
        console.log(`   WC_CONSUMER_KEY: ${process.env.WC_CONSUMER_KEY ? '✓' : '✗'}`);
    });

    if (process.env.BOT_TOKEN && process.env.BOT_TOKEN !== 'TU_TOKEN_AQUI') {
        try {
            const { startBot } = require('./bot');
            startBot();
        } catch (err) {
            console.error('❌ Error iniciando bot:', err.message);
        }
    } else {
        console.log('⚠️  BOT_TOKEN no configurado. Bot desactivado.');
    }
});
