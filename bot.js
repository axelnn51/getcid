const { Telegraf } = require('telegraf');
const fs = require('fs');
const path = require('path');
const { getConfirmationID, CIDError } = require('./cid_helper');
const { ocrAndGetCID } = require('./ocr');
const db = require('./db');

const BOT_TOKEN = process.env.BOT_TOKEN;
const ADMIN_IDS = (process.env.ADMIN_IDS || '').split(',').map(s => s.trim()).filter(Boolean);

// ============================================================
// Rate limiting por usuario (máx 3 requests/minuto)
// ============================================================
const userCooldowns = new Map();
const RATE_LIMIT_WINDOW = 60 * 1000; // 1 minuto
const RATE_LIMIT_MAX = 3;

function checkRateLimit(userId) {
    const now = Date.now();
    const key = String(userId);
    if (!userCooldowns.has(key)) userCooldowns.set(key, []);
    
    const timestamps = userCooldowns.get(key).filter(t => now - t < RATE_LIMIT_WINDOW);
    userCooldowns.set(key, timestamps);
    
    if (timestamps.length >= RATE_LIMIT_MAX) {
        const waitSecs = Math.ceil((timestamps[0] + RATE_LIMIT_WINDOW - now) / 1000);
        return { limited: true, waitSecs };
    }
    
    timestamps.push(now);
    return { limited: false };
}

// Limpiar cooldowns cada 5 minutos
setInterval(() => {
    const now = Date.now();
    for (const [key, timestamps] of userCooldowns) {
        const valid = timestamps.filter(t => now - t < RATE_LIMIT_WINDOW);
        if (valid.length === 0) userCooldowns.delete(key);
        else userCooldowns.set(key, valid);
    }
}, 5 * 60 * 1000);

// ============================================================
// Formateo
// ============================================================
function formatIID(iid) { return iid.match(/.{1,7}/g)?.join('-') || iid; }
function formatCID(cid) {
    if (Array.isArray(cid)) return cid.join('-');
    if (typeof cid === 'string') return cid.match(/.{1,6}/g)?.join('-') || cid;
    return JSON.stringify(cid);
}

// ============================================================
// Mensajes de error descriptivos
// ============================================================
function errorToMessage(error, detectedIID) {
    const iid = error?.iid || detectedIID || null;
    const iidBlock = iid ? `\n\n📝 IID detectado:\n<code>${formatIID(iid)}</code>` : '';

    // CIDError con código específico
    if (error instanceof CIDError || (error && error.code)) {
        const code = error.code;
        
        switch(code) {
            case 'INVALID_CHECKSUM':
                return `❌ <b>IID con checksum inválido</b>\nUn dígito está incorrecto. Verifica cada bloque de 7 dígitos contra tu pantalla.${iidBlock}`;
            
            case 'KEY_BLOCKED':
                return `🔒 <b>Clave bloqueada por Microsoft</b>\nEsta licencia ha sido bloqueada. Contacta a soporte para un reemplazo.${iidBlock}`;
            
            case 'TOO_MANY_ACTIVATIONS':
                return `⚠️ <b>Límite de activaciones alcanzado</b>\nEsta licencia ya se activó en demasiados dispositivos. Contacta soporte.${iidBlock}`;
            
            case 'INVALID_PRODUCT':
                return `❌ <b>Producto no soportado</b>\nEste IID corresponde a un producto que no se puede activar por teléfono.${iidBlock}`;
            
            case 'KEY_EXPIRED':
                return `⏰ <b>Licencia expirada</b>\nEsta licencia ha expirado. Contacta soporte.${iidBlock}`;
            
            case 'KEY_NOT_GENUINE':
                return `🚫 <b>Licencia no válida</b>\nMicrosoft no reconoce esta licencia como genuina.${iidBlock}`;
            
            case 'GRACE_PERIOD':
                return `⏳ <b>Período de gracia</b>\nEl producto está en prueba. Instala una licencia válida primero.${iidBlock}`;
            
            case 'ACTIVATION_FAILED':
                return `❌ <b>Activación rechazada</b>\nMicrosoft rechazó la activación de este IID.${iidBlock}`;
            
            case 'IID_TOO_SHORT':
                return `❌ <b>IID demasiado corto</b>\nSe detectaron ${iid?.length || '?'} dígitos, se necesitan 54-63.${iidBlock}`;
            
            case 'IID_TOO_LONG':
                return `❌ <b>IID demasiado largo</b>\nSe detectaron ${iid?.length || '?'} dígitos, el máximo es 63.${iidBlock}`;
            
            case 'TIMEOUT':
                return `⏱ <b>Tiempo agotado</b>\nMicrosoft no respondió en 15s. Intenta de nuevo.${iidBlock}`;
            
            case 'NETWORK_ERROR':
                return `🌐 <b>Error de conexión</b>\nNo se pudo conectar con Microsoft. Intenta más tarde.${iidBlock}`;
            
            case 'NO_CID_IN_RESPONSE':
                return `❌ <b>Sin CID en respuesta</b>\nMicrosoft respondió pero no incluyó Confirmation ID.${iidBlock}`;
            
            default:
                if (code.startsWith('MS_HTTP_')) {
                    const status = code.replace('MS_HTTP_', '');
                    if (status === '403') return `🔒 <b>Error 403 — Acceso denegado</b>\nMicrosoft rechazó la solicitud. El IID podría estar bloqueado o ser de un producto no soportado.${error.userMessage ? '\n\n<i>' + error.userMessage + '</i>' : ''}${iidBlock}`;
                    if (status === '429') return `⏳ <b>Error 429 — Demasiadas solicitudes</b>\nEspera 1-2 minutos e intenta de nuevo.${iidBlock}`;
                    return `❌ <b>Error Microsoft ${status}</b>\n${error.userMessage || 'Error del servidor de activación.'}${iidBlock}`;
                }
                return `❌ <b>Error: ${code}</b>\n${error.userMessage || error.message || 'Error desconocido.'}${iidBlock}`;
        }
    }

    // Error genérico de checksum (legado)
    if (error?.message === 'INVALID_CHECKSUM') {
        return `❌ <b>Checksum inválido</b>\nVerifica el IID.${iidBlock}`;
    }

    return `❌ <b>Error inesperado</b>\n${error?.message || 'Error desconocido.'}${iidBlock}`;
}

function startBot() {
    if (!BOT_TOKEN) {
        console.error('❌ BOT_TOKEN está vacío. Bot no iniciará.');
        return;
    }

    console.log(`🤖 Iniciando bot con token: ${BOT_TOKEN.substring(0, 10)}...`);
    console.log(`🔑 Admin IDs: ${ADMIN_IDS.join(', ') || 'NINGUNO'}`);

    const bot = new Telegraf(BOT_TOKEN);

    function isAdmin(tgId) {
        return ADMIN_IDS.includes(String(tgId));
    }

    // /start
    bot.start((ctx) => {
        const tgId = String(ctx.from.id);
        const username = ctx.from.username || ctx.from.first_name;
        let user = db.findUserByTelegram(tgId);
        if (!user) user = db.createUser({ telegram_id: tgId, telegram_username: username });
        if (isAdmin(tgId) && !user.is_admin) { db.setAdmin(user.id); user = db.findUserByTelegram(tgId); }

        const tag = user.is_admin ? ' 👑 Admin' : '';
        ctx.reply(
            `👋 ¡Hola ${username}!${tag}\n\n` +
            `📸 *Envíame una foto* del asistente de activación\n` +
            `📝 O *escribe el IID* (63 dígitos)\n\n` +
            `💰 Balance: *${user.balance} CIDs*` +
            (user.is_admin ? `\n\n⚙️ Admin:\n/addcredits <id> <n>\n/stats` : ''),
            { parse_mode: 'Markdown' }
        );
    });

    bot.command('balance', (ctx) => {
        const user = db.findUserByTelegram(String(ctx.from.id));
        if (!user) return ctx.reply('Usa /start primero.');
        ctx.reply(`💰 Balance: *${user.balance} CIDs*`, { parse_mode: 'Markdown' });
    });

    bot.command('addcredits', (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');
        const admin = db.findUserByTelegram(tgId);
        const args = ctx.message.text.split(' ').slice(1);
        if (args.length < 2) return ctx.reply('Uso: /addcredits <telegram_id> <cantidad>');
        const targetId = args[0], amount = parseInt(args[1]);
        if (isNaN(amount) || amount <= 0) return ctx.reply('❌ Cantidad inválida.');
        let target = db.findUserByTelegram(targetId);
        if (!target) target = db.createUser({ telegram_id: targetId, telegram_username: 'unknown' });
        const bal = db.addCredits(target.id, amount, 'admin_tg', admin ? admin.id : null);
        ctx.reply(`✅ +${amount} créditos → ${targetId}\nBalance: *${bal}*`, { parse_mode: 'Markdown' });
    });

    bot.command('stats', (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.reply('❌ No tienes permisos.');
        const s = db.getStats();
        ctx.reply(`📊 Usuarios: ${s.totalUsers}\nCIDs hoy: ${s.todayCids}\nCIDs total: ${s.totalCids}`);
    });

    // ============================================================
    // FOTOS — Con mensajes de error descriptivos
    // ============================================================
    bot.on('photo', async (ctx) => {
        const tgId = String(ctx.from.id);
        let user = db.findUserByTelegram(tgId);
        if (!user) user = db.createUser({ telegram_id: tgId, telegram_username: ctx.from.username });
        if (user.balance <= 0) return ctx.reply('❌ Sin créditos. Contacta al admin.');

        // Rate limit
        const rl = checkRateLimit(tgId);
        if (rl.limited) return ctx.reply(`⏳ Espera ${rl.waitSecs}s antes de enviar otra solicitud.`);

        const startTime = Date.now();
        const msg = await ctx.reply('⏳ Procesando...');

        try {
            const photo = ctx.message.photo[ctx.message.photo.length - 1];
            const file = await ctx.telegram.getFile(photo.file_id);
            const filePath = path.join(__dirname, 'uploads', `tg_${Date.now()}.jpg`);
            if (!fs.existsSync(path.join(__dirname, 'uploads'))) fs.mkdirSync(path.join(__dirname, 'uploads'), { recursive: true });

            const resp = await fetch(`https://api.telegram.org/file/bot${BOT_TOKEN}/${file.file_path}`);
            fs.writeFileSync(filePath, Buffer.from(await resp.arrayBuffer()));

            const result = await ocrAndGetCID(filePath, getConfirmationID);
            if (fs.existsSync(filePath)) fs.unlinkSync(filePath);

            const elapsed = Date.now() - startTime;
            const secs = (elapsed / 1000).toFixed(0).padStart(2, '0');

            if (result.success) {
                db.debitCredit(user.id);
                const bal = db.getBalance(user.id);
                const cidStr = formatCID(result.cid);
                db.logTransaction(user.id, 'telegram', result.iid, cidStr, 'success', elapsed, result.strategy);
                
                // Mensaje principal
                await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null,
                    `🔑 <b>@CdKeysPeru</b>\n\n` +
                    `<b>IID:</b>\n<code>${formatIID(result.iid)}</code>\n\n` +
                    `<b>CID:</b>\n<code>${cidStr}</code>\n\n` +
                    `<i>💰 -1 CID | Balance: ${bal} | ⏱ 00:${secs}</i>`,
                    { parse_mode: 'HTML' }
                );
                
                // Enviar CID como texto separado para fácil copiado
                await ctx.reply(`📋 CID para copiar:\n<code>${cidStr.replace(/-/g, '')}</code>`, { parse_mode: 'HTML' });
            } else {
                // OCR falló — mostrar dígitos detectados si hay
                const detectedDigits = result.detectedDigits;
                db.logTransaction(user.id, 'telegram', detectedDigits, null, 'ocr_failed', elapsed, null);
                
                let errorMsg = '❌ <b>No se pudo detectar el IID en la imagen</b>\n';
                if (detectedDigits) {
                    errorMsg += `\n📝 Dígitos detectados (${detectedDigits.length}):\n<code>${formatIID(detectedDigits)}</code>\n`;
                    errorMsg += '\nVerifica contra tu pantalla. Si son correctos, envíalos como texto.';
                } else {
                    errorMsg += '\nIntenta con foto más nítida o envía el IID como texto.';
                }
                
                await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, errorMsg, { parse_mode: 'HTML' });
            }
        } catch (err) {
            console.error('[BOT PHOTO ERROR]', err.code || err.message);
            const elapsed = Date.now() - startTime;
            db.logTransaction(user.id, 'telegram', err?.iid || null, null, err?.code || 'error', elapsed, null);
            
            const errorMsg = errorToMessage(err, err?.iid);
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, errorMsg, { parse_mode: 'HTML' }).catch(() => {});
        }
    });

    // ============================================================
    // TEXTO (IID directo) — Con mensajes de error descriptivos
    // ============================================================
    bot.on('text', async (ctx) => {
        if (ctx.message.text.startsWith('/')) return;
        const digits = ctx.message.text.replace(/\D/g, '');
        
        // Feedback para texto que parece un IID pero es muy corto
        if (digits.length >= 20 && digits.length < 54) {
            return ctx.reply(
                `⚠️ <b>IID incompleto</b>\nSe detectaron ${digits.length} dígitos, se necesitan al menos 54 (9 bloques de 6-7 dígitos).\n\n` +
                `📝 Dígitos detectados:\n<code>${formatIID(digits)}</code>`,
                { parse_mode: 'HTML' }
            );
        }
        
        if (digits.length < 54) return; // Ignorar mensajes no relevantes

        const tgId = String(ctx.from.id);
        let user = db.findUserByTelegram(tgId);
        if (!user) user = db.createUser({ telegram_id: tgId, telegram_username: ctx.from.username });
        if (user.balance <= 0) return ctx.reply('❌ Sin créditos.');

        // Rate limit
        const rl = checkRateLimit(tgId);
        if (rl.limited) return ctx.reply(`⏳ Espera ${rl.waitSecs}s antes de enviar otra solicitud.`);

        const startTime = Date.now();
        const msg = await ctx.reply('⏳ Obteniendo CID...');

        try {
            const cid = await getConfirmationID(digits);
            const elapsed = Date.now() - startTime;
            const secs = (elapsed / 1000).toFixed(0).padStart(2, '0');
            db.debitCredit(user.id);
            const bal = db.getBalance(user.id);
            const cidStr = formatCID(cid);
            db.logTransaction(user.id, 'telegram', digits, cidStr, 'success', elapsed, 'direct');
            
            // Mensaje principal
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null,
                `🔑 <b>@CdKeysPeru</b>\n\n` +
                `<b>IID:</b>\n<code>${formatIID(digits)}</code>\n\n` +
                `<b>CID:</b>\n<code>${cidStr}</code>\n\n` +
                `<i>💰 -1 CID | Balance: ${bal} | ⏱ 00:${secs}</i>`,
                { parse_mode: 'HTML' }
            );
            
            // CID como texto separado para copiar
            await ctx.reply(`📋 CID para copiar:\n<code>${cidStr.replace(/-/g, '')}</code>`, { parse_mode: 'HTML' });
        } catch (err) {
            const elapsed = Date.now() - startTime;
            db.logTransaction(user.id, 'telegram', digits, null, err?.code || 'error', elapsed, null);
            
            const errorMsg = errorToMessage(err, digits);
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, errorMsg, { parse_mode: 'HTML' });
        }
    });

    // CALLBACK: info
    bot.on('callback_query', async (ctx) => {
        await ctx.answerCbQuery('ℹ️', { show_alert: false });
    });

    // ARRANCAR
    bot.launch()
        .then(() => {
            console.log('🤖 Bot de Telegram iniciado correctamente');

            // Notificar al admin que el bot está en línea
            for (const adminId of ADMIN_IDS) {
                bot.telegram.sendMessage(adminId,
                    `🟢 *GETCID Bot en línea*\n\n` +
                    `Servidor iniciado: ${new Date().toLocaleString('es-PE')}\n` +
                    `Motor OCR: ✅ Cargado\n` +
                    `Web: ✅ Puerto ${process.env.PORT || 3000}\n` +
                    `WooCommerce: ${process.env.WC_CONSUMER_KEY ? '✅' : '❌'}\n\n` +
                    `Comandos admin:\n/addcredits <id> <n>\n/stats`,
                    { parse_mode: 'Markdown' }
                ).catch(err => console.log(`⚠️ No se pudo notificar al admin ${adminId}: ${err.message}`));
            }
        })
        .catch(err => {
            console.error('❌ Error al iniciar bot de Telegram:', err.message);
            if (err.message.includes('401') || err.message.includes('404')) {
                console.error('   → Token inválido. Regenera el token en @BotFather');
            }
        });

    process.once('SIGINT', () => bot.stop('SIGINT'));
    process.once('SIGTERM', () => bot.stop('SIGTERM'));
}

// Exportar la función
module.exports = { startBot };
