// dotenv es opcional (en Docker las vars vienen del entorno)
try { require('dotenv').config(); } catch(e) {}
const express = require('express');
const cors = require('cors');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const rateLimit = require('express-rate-limit');
const { getConfirmationID } = require('./cid_helper');
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
// API: OCR rápido local (sin créditos)
// ============================================================
app.post('/api/process-image', upload.single('image'), async (req, res) => {
    if (!req.file) return res.status(400).json({ success: false, error: 'No image' });
    try {
        const result = await ocrAndGetCID(req.file.path, getConfirmationID);
        if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        if (result.success) return res.json(result);
        return res.json({ success: false, error: 'No se pudo extraer el IID o checksum inválido.' });
    } catch (e) {
        if (fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        res.status(500).json({ success: false, error: e.message });
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

async function syncAndGetUser(identifier) {
    const isOrderNumber = /^\d+$/.test(identifier);
    let resolvedEmail = identifier;

    if (isOrderNumber && wc.isConfigured()) {
        const order = await wc.getOrderById(identifier);
        if (order && order.status === 'completed') {
            resolvedEmail = order.billing?.email?.toLowerCase() || `order_${identifier}`;
            if (!db.isOrderUsed(identifier)) {
                let u = db.findUserByEmail(resolvedEmail);
                if (!u) { u = db.createUser({ email: resolvedEmail }); }
                db.markOrderUsed(identifier);
                db.addCredits(u.id, 1, 'woocommerce_order_' + identifier);
            }
        }
    }

    let user = db.findUserByEmail(resolvedEmail);

    if (resolvedEmail.includes('@') && wc.isConfigured()) {
        const orders = await wc.getOrdersByEmail(resolvedEmail);
        if (orders) {
            for (const order of orders) {
                if (order.status === 'completed' && !db.isOrderUsed(order.id)) {
                    if (!user) { user = db.createUser({ email: resolvedEmail }); }
                    db.markOrderUsed(order.id);
                    db.addCredits(user.id, 1, 'woocommerce_order_' + order.id);
                }
            }
        }
    }

    return db.findUserByEmail(resolvedEmail);
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
            db.logTransaction(user.id, 'web', null, null, 'failed', elapsed, null);
            return res.status(400).json({ success: false, error: 'No se pudo obtener el CID. Verifica tu IID.' });
        }
    } catch (e) {
        if (req.file && fs.existsSync(req.file.path)) fs.unlinkSync(req.file.path);
        return res.status(400).json({ success: false, error: e.message === 'INVALID_CHECKSUM' ? 'IID inválido (checksum).' : 'Error procesando solicitud.' });
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
