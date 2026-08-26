const { Telegraf, Markup } = require('telegraf');
const fs = require('fs');
const path = require('path');
const { getConfirmationID, CIDError } = require('./cid_helper');
const { ocrAndGetCID } = require('./ocr');
const db = require('./db');

const BOT_TOKEN = process.env.BOT_TOKEN;
const ADMIN_IDS = (process.env.ADMIN_IDS || '').split(',').map(s => s.trim()).filter(Boolean);
const GETCID_SERVICE_URL = process.env.GETCID_SERVICE_URL || 'http://getcid_python:8000';

// Bot instance global para notificaciones desde web
let botInstance = null;

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
    if (typeof cid === 'string') {
        // Limpiar todo excepto dígitos, luego formatear en bloques de 6
        const digits = cid.replace(/\D/g, '');
        return digits.match(/.{1,6}/g)?.join('-') || digits;
    }
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
                return `🌐 <b>Error de conexión</b>\n${error.userMessage || 'No se pudo conectar con Microsoft.'}\nIntenta más tarde.${iidBlock}`;
            
            case 'NO_CID_IN_RESPONSE':
                return `❌ <b>Sin CID en respuesta</b>\nMicrosoft respondió pero no incluyó Confirmation ID.${iidBlock}`;
            
            default:
                if (code.startsWith('MS_HTTP_')) {
                    const status = code.replace('MS_HTTP_', '');
                    if (status === '403') return `🔒 <b>Error 403 — Acceso denegado</b>\nMicrosoft rechazó la solicitud. El IID podría estar bloqueado o ser de un producto no soportado.${iidBlock}`;
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
        let extraOptions = { parse_mode: 'Markdown' };
        
        if (user.is_admin) {
            extraOptions = {
                parse_mode: 'Markdown',
                ...Markup.keyboard([
                    ['🔄 Renovar Token', '📊 Estado Sistema'],
                    ['🔑 Estado Token', '👥 Usuarios'],
                    ['⚙️ Ayuda Admin']
                ]).resize()
            };
        }

        ctx.reply(
            `👋 ¡Hola ${username}!${tag}\n\n` +
            `📸 *Envíame una foto* del asistente de activación\n` +
            `📝 O *escribe el IID* (63 dígitos)\n\n` +
            `💰 Balance: *${user.balance} CIDs*`,
            extraOptions
        );
    });

    // ============================================================
    // HANDLERS DEL MENÚ RIBBON (Admin)
    // ============================================================
    bot.hears('🔄 Renovar Token', async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return;
        const msg = await ctx.reply('⚙️ Despertando al Auto-Extractor indetectable (VNC)... Por favor espera.', { parse_mode: 'Markdown' });
        
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/api/force_extraction`, { method: 'POST' });
            const data = await response.json();
            if (!data.success) {
                ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, `❌ Error: ${data.error}`);
            } else {
                ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, `✅ Auto-Extractor iniciado en el servidor.\nSi hay algún bloqueo de MS, recibirás el link de Cloudflare en unos segundos.`);
            }
        } catch (err) {
            ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, `❌ No se pudo contactar al servidor: ${err.message}`);
        }
    });

    bot.hears('📊 Estado Sistema', async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return;
        
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/status`);
            const data = await response.json();
            
            let msg = `📊 *ESTADO DEL SISTEMA*\n\n`;
            msg += `🔌 *API:* ${data.api_status === 'online' ? '🟢 Online' : '🔴 Offline'}\n`;
            msg += `🤖 *Demonio:* ${data.daemon_status === 'running' ? '🟢 Activo' : (data.daemon_status === 'failed' ? '🔴 Falló' : '⚪ Inactivo')}\n`;
            msg += `🔑 *Access Token:* ${data.has_access_token ? '🟢 OK' : '🔴 Falta'}\n`;
            msg += `🔄 *Refresh Token:* ${data.has_refresh_token ? '🟢 OK' : '🔴 Falta'}\n`;
            if (data.daemon_error) msg += `⚠️ *Error:* ${data.daemon_error}\n`;
            
            const s = db.getStats();
            msg += `\n👥 *Usuarios:* ${s.totalUsers}\n`;
            msg += `📈 *CIDs Hoy:* ${s.todayCids}\n`;
            
            ctx.reply(msg, { parse_mode: 'Markdown' });
        } catch (err) {
            ctx.reply(`❌ Error conectando al servidor: ${err.message}`);
        }
    });

    bot.hears('👥 Usuarios', (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return;
        ctx.reply('Para gestionar usuarios usa los comandos manuales por ahora:\n`/addcredits <id> <n>`', { parse_mode: 'Markdown' });
    });

    bot.hears('⚙️ Ayuda Admin', (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return;
        ctx.reply(
            '⚙️ *Comandos Admin Disponibles:*\n\n' +
            '🔄 *Renovar Token* — Dispara el Auto-Extractor remoto (VNC/Cloudflare) para renovar el token\n' +
            '📊 *Estado Sistema* — Resumen rápido del servidor y tokens\n' +
            '🔑 *Estado Token* — Detalles técnicos extendidos\n' +
            '👥 *Usuarios* — Ver usuarios actuales\n\n' +
            '*Comandos manuales:*\n' +
            '/systemstatus — Estado completo del servidor (JSON)\n' +
            '/addcredits <id> <n> — Agregar créditos a un usuario\n' +
            '/stats — Estadísticas globales',
            { parse_mode: 'Markdown' }
        );
    });

    bot.command('balance', (ctx) => {
        const user = db.findUserByTelegram(String(ctx.from.id));
        if (!user) return ctx.reply('Usa /start primero.');
        ctx.reply(`💰 Balance: *${user.balance} CIDs*`, { parse_mode: 'Markdown' });
    });

    const PID_CHECKER_URL = process.env.PID_CHECKER_URL || 'http://localhost:8080';

    bot.command('check', async (ctx) => {
        const args = ctx.message.text.split(' ').slice(1);
        if (args.length === 0) {
            return ctx.reply(
                '🔑 *Verificador de Licencias (PID Checker)*\n\n' +
                'Uso: `/check XXXXX-XXXXX-XXXXX-XXXXX-XXXXX`\n\n' +
                '🔍 Verifica estado real contra servidores de Microsoft.',
                { parse_mode: 'Markdown' }
            );
        }

        const key = args[0].toUpperCase();
        const keyRegex = /^([A-Z0-9]{5}-){4}[A-Z0-9]{5}$/;
        if (!keyRegex.test(key)) {
            return ctx.reply('❌ *Formato inválido*. La clave debe tener 25 caracteres (ej. XXXXX-XXXXX-XXXXX-XXXXX-XXXXX).', { parse_mode: 'Markdown' });
        }

        const msg = await ctx.reply('⏳ Verificando clave contra servidores de Microsoft...');

        try {
            const response = await fetch(`${PID_CHECKER_URL}/api/v1/check`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: key })
            });

            if (response.status === 429) {
                return ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, '⏳ Demasiadas verificaciones. Espera un minuto.');
            }

            if (response.status === 503) {
                return ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, '❌ Motor PID Checker no disponible. Contacta al admin.');
            }

            const data = await response.json();
            let resultMsg = `🔑 <b>Reporte de Licencia</b>\n\n<b>Clave:</b> <code>${data.key}</code>\n`;

            if (data.edition) resultMsg += `<b>Edición:</b> ${data.edition}\n`;
            if (data.key_type) resultMsg += `<b>Tipo:</b> ${data.key_type}\n`;

            resultMsg += `\n`;

            if (data.is_valid) {
                resultMsg += `✅ <b>Estado:</b> Online-Valid (Clave válida)\n`;
            } else {
                resultMsg += `❌ <b>Estado:</b> Inválida\n`;
                if (data.error_code) resultMsg += `<b>Código:</b> ${data.error_code}\n`;
                if (data.error_message) resultMsg += `<b>Motivo:</b> ${data.error_message}\n`;
            }

            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, resultMsg, { parse_mode: 'HTML' });

        } catch (error) {
            console.error('[PID CHECKER ERROR]', error);
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, '❌ No se pudo conectar con el motor del verificador. Intenta más tarde.');
        }
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
    // CARGA DE SESSION_MASTER EN CALIENTE
    // ============================================================
    bot.on('document', async (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return;
        
        const doc = ctx.message.document;
        if (doc.file_name !== 'session_master.json') {
            return ctx.reply('❌ Solo acepto archivos llamados `session_master.json`.', { parse_mode: 'Markdown' });
        }
        
        try {
            const msg = await ctx.reply('⏳ Procesando y actualizando sesión en caliente...');
            
            const file = await ctx.telegram.getFile(doc.file_id);
            const resp = await fetch(`https://api.telegram.org/file/bot${BOT_TOKEN}/${file.file_path}`);
            const sessionData = await resp.json();
            
            if (!sessionData.tokens_network) {
                return ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, '❌ Archivo inválido. No contiene `tokens_network`.');
            }
            
            const updateResp = await fetch(`${GETCID_SERVICE_URL}/api/update_session`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sessionData)
            });
            
            const result = await updateResp.json();
            
            if (result.success) {
                await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, `✅ *Sesión Actualizada Exitosamente*\n\nEl backend ahora usará los nuevos tokens.`, { parse_mode: 'Markdown' });
            } else {
                await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, `❌ Falló la actualización: ${result.error}`);
            }
            
        } catch (err) {
            ctx.reply(`❌ Error al cargar la sesión: ${err.message}`);
        }
    });

    // ============================================================
    // /systemstatus — Estado completo del sistema
    // ============================================================
    bot.command('systemstatus', async (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');
        
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/status`);
            const data = await response.json();
            
            const stats = db.getStats();
            
            ctx.reply(
                `📊 *Estado del Sistema (Extendido)*\n` +
                `📅 ${new Date().toLocaleString('es-PE')}\n\n` +
                `🔌 API Status: ${data.api_status}\n` +
                `🤖 Daemon: ${data.daemon_status}\n` +
                `🔑 Access Token: ${data.has_access_token ? 'Presente' : 'Ausente'}\n` +
                `🔄 Refresh Token: ${data.has_refresh_token ? 'Presente' : 'Ausente'}\n` +
                (data.daemon_error ? `⚠️ Error: ${data.daemon_error}\n\n` : '\n') +
                `📈 CIDs hoy: *${stats.todayCids}*\n` +
                `📈 CIDs total: *${stats.totalCids}*\n` +
                `👥 Usuarios: *${stats.totalUsers}*\n` +
                `⏱ Uptime: ${Math.floor(process.uptime() / 3600)}h`,
                { parse_mode: 'Markdown' }
            );
        } catch (err) {
            ctx.reply(`❌ Error consultando estado: ${err.message}`);
        }
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
                    `<i>💰 -1 CID | Balance: ${bal} | ⏱ 00:${secs}</i>\n` +
                    `<i>🤖 Resuelto vía: ${result.backendMethod || 'Desconocido'} (OCR: ${result.strategy})</i>`,
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
            const cidResult = await getConfirmationID(digits);
            const cid = typeof cidResult === 'string' ? cidResult : cidResult.cid;
            const backendMethod = typeof cidResult === 'object' ? cidResult.method : 'unknown';
            
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
                `<i>💰 -1 CID | Balance: ${bal} | ⏱ 00:${secs}</i>\n` +
                `<i>🤖 Resuelto vía: ${backendMethod}</i>`,
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

    // ============================================================
    // HANDLERS DE MENÚ: Estado Token y Device Auth
    // ============================================================
    bot.hears('🔑 Estado Token', async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return;
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/status`);
            const data = await response.json();
            
            ctx.reply(
                `🔑 *Estado de Tokens (Backend)*\n\n` +
                `🔑 Access Token: ${data.has_access_token ? '🟢 Cargado en memoria' : '🔴 No existe'}\n` +
                `🔄 Refresh Token: ${data.has_refresh_token ? '🟢 Cargado en memoria' : '🔴 No existe'}\n` +
                `🤖 Auto-Renovador: ${data.daemon_status === 'running' ? '✅ Corriendo' : (data.daemon_status === 'failed' ? '❌ Falló' : '⚪ Inactivo')}\n` +
                (data.daemon_error ? `\n⚠️ Error: ${data.daemon_error}` : ''),
                { parse_mode: 'Markdown' }
            );
        } catch (err) {
            ctx.reply(`❌ Error: ${err.message}`);
        }
    });

    // CALLBACK: info
    bot.on('callback_query', async (ctx) => {
        await ctx.answerCbQuery('ℹ️', { show_alert: false });
    });

    // Guardar referencia global para notificaciones
    botInstance = bot;

    // ============================================================
    // HEALTH CHECK DIARIO — 3:00 PM hora Perú (UTC-5)
    // ============================================================
    let lastHealthDay = '';
    setInterval(async () => {
        const now = new Date();
        // Convertir a hora Perú (UTC-5)
        const peruTime = new Date(now.getTime() - 5 * 60 * 60 * 1000);
        const hour = peruTime.getUTCHours();
        const minute = peruTime.getUTCMinutes();
        const today = peruTime.toISOString().split('T')[0];
        
        // Disparar a las 15:00 (3PM), solo una vez por día
        if (hour === 15 && minute === 0 && lastHealthDay !== today) {
            lastHealthDay = today;
            
            try {
                // Verificar estado del token
                let tokenStatus = '❓ Desconocido';
                let refreshStatus = '❓ Desconocido';
                let refresherStatus = '❌ Inactivo';
                try {
                    const sysResp = await fetch(`${GETCID_SERVICE_URL}/status`);
                    const sysData = await sysResp.json();
                    
                    tokenStatus = sysData.has_access_token ? '🟢 OK' : '🔴 Falta';
                    refreshStatus = sysData.has_refresh_token ? '🟢 OK' : '🔴 Falta';
                    refresherStatus = sysData.daemon_status === 'running' ? '✅ Activo' : '❌ Falló';
                } catch(e) { /* ignore */ }
                
                const stats = db.getStats();
                const uptime = Math.floor(process.uptime() / 3600);
                
                for (const adminId of ADMIN_IDS) {
                    bot.telegram.sendMessage(adminId,
                        `📊 *Reporte Diario GetCID*\n` +
                        `📅 ${today} | 3:00 PM\n\n` +
                        `🔑 Access Token: ${tokenStatus}\n` +
                        `🔄 Refresh Token: ${refreshStatus}\n` +
                        `⚙️ Proactive Refresh: ${refresherStatus}\n\n` +
                        `📈 CIDs hoy: *${stats.todayCids}*\n` +
                        `📈 CIDs total: *${stats.totalCids}*\n` +
                        `👥 Usuarios: *${stats.totalUsers}*\n` +
                        `⏱ Uptime: ${uptime}h`,
                        { parse_mode: 'Markdown' }
                    ).catch(() => {});
                }
            } catch(e) {
                console.error('[HEALTH CHECK ERROR]', e.message);
            }
        }
    }, 60 * 1000); // Verificar cada minuto

    // ARRANCAR
    // Configurar menú desplegable de comandos
    bot.telegram.setMyCommands([
        { command: 'start', description: 'Menú principal' },
        { command: 'help', description: 'Ayuda sobre uso' },
        { command: 'cid', description: 'Obtener CID (uso: /cid [IID])' },
        { command: 'check', description: 'Revisar Key' },
        { command: 'balance', description: 'Ver tus créditos' },
        { command: 'addcredits', description: 'Admin: Agregar créditos' },
        { command: 'tokenstatus', description: 'Admin: Estado del token de Microsoft' },
        { command: 'systemstatus', description: 'Admin: Ver contenedores y servicios' },
        { command: 'deviceauth', description: 'Admin: Login interactivo' },
        { command: 'settoken', description: 'Admin: Forzar Access Token manual' },
        { command: 'setrefreshtoken', description: 'Admin: Cargar Refresh Token' },
        { command: 'revert', description: 'Admin: Revertir a token anterior' },
        { command: 'stats', description: 'Admin: Estadísticas de uso' },
        { command: 'startrenovation', description: 'Admin: Forzar Auto-Renovación' },
        { command: 'restart', description: 'Admin: Reiniciar el bot' }
    ]).catch(err => console.log('⚠️ Error configurando comandos:', err.message));

    // Comandos para reanudar el sistema manualmente
    const unpauseHandler = async (ctx) => {
        if (!ADMIN_IDS.includes(String(ctx.from.id))) return;
        try {
            await ctx.reply('🔄 Eliminando estado de pausa y disparando Playwright...');
            // 1. Quitar la pausa
            await fetch(`${GETCID_SERVICE_URL}/api/system-pause`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paused: false })
            });
            // 2. Disparar renovación
            const resp = await fetch(`${GETCID_SERVICE_URL}/api/start-renovation`, { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                await ctx.reply('✅ ' + data.message);
            } else {
                await ctx.reply('⚠️ Resultado: ' + data.error);
            }
        } catch (e) {
            await ctx.reply('❌ Error comunicándose con la API: ' + e.message);
        }
    };
    bot.command('startrenovation', unpauseHandler);
    bot.command('unpause', unpauseHandler);

    // Comando oculto para reiniciar el bot
    bot.command('restart', async (ctx) => {
        if (!ADMIN_IDS.includes(String(ctx.from.id))) return;
        await ctx.reply('🔄 Reiniciando servidor Node.js. Volveré en unos segundos...');
        process.exit(1);
    });

    // ─── AUTO-RECONEXIÓN ANTI-CAÍDAS ───
    // Si el polling de Telegram muere (error de red, 409 Conflict, timeout),
    // el bot se reconecta automáticamente con backoff exponencial
    let reconnectAttempts = 0;
    const MAX_RECONNECT_DELAY = 60000; // 60 segundos máximo
    
    async function launchWithAutoReconnect() {
        while (true) {
            try {
                // Si hay otra instancia usando el mismo token (409 Conflict),
                // primero limpiamos el webhook/getUpdates viejo
                if (reconnectAttempts > 0) {
                    try {
                        await bot.telegram.deleteWebhook({ drop_pending_updates: true });
                    } catch (e) { /* ignore */ }
                    
                    const delay = Math.min(5000 * Math.pow(2, reconnectAttempts - 1), MAX_RECONNECT_DELAY);
                    console.log(`🔄 [RECONNECT] Intento ${reconnectAttempts} — esperando ${delay/1000}s...`);
                    await new Promise(r => setTimeout(r, delay));
                }
                
                await bot.launch({ dropPendingUpdates: true });
                
                // Si llegamos aquí, el bot se conectó exitosamente
                if (reconnectAttempts > 0) {
                    console.log(`✅ [RECONNECT] Bot reconectado tras ${reconnectAttempts} intentos.`);
                    for (const adminId of ADMIN_IDS) {
                        bot.telegram.sendMessage(adminId,
                            `🟢 *Bot RECONECTADO*\n\n` +
                            `Tras ${reconnectAttempts} intentos de reconexión.\n` +
                            `Hora: ${new Date().toLocaleString('es-PE')}`,
                            { parse_mode: 'Markdown' }
                        ).catch(() => {});
                    }
                }
                reconnectAttempts = 0;
                
                console.log('🤖 Bot de Telegram iniciado correctamente');

                // Notificar al admin que el bot está en línea
                for (const adminId of ADMIN_IDS) {
                    bot.telegram.sendMessage(adminId,
                        `🟢 *GETCID Bot en línea*\n\n` +
                        `Servidor iniciado: ${new Date().toLocaleString('es-PE')}\n` +
                        `Motor OCR: ✅ Cargado\n` +
                        `Web: ✅ Puerto ${process.env.PORT || 3000}\n` +
                        `WooCommerce: ${process.env.WC_CONSUMER_KEY ? '✅' : '❌'}\n\n` +
                        `Comandos admin:\n` +
                        `/addcredits <id> <n>\n` +
                        `/stats\n` +
                        `/tokenstatus\n` +
                        `/systemstatus\n` +
                        `/deviceauth\n` +
                        `/settoken <token>\n` +
                        `/setrefreshtoken <json>`,
                        { parse_mode: 'Markdown' }
                    ).catch(err => console.log(`⚠️ No se pudo notificar al admin ${adminId}: ${err.message}`));
                }
                
                // El bot queda en polling infinito. Solo salimos de este while si bot.launch() finaliza sin error.
                break;
                
            } catch (err) {
                reconnectAttempts++;
                console.error(`❌ [RECONNECT] Error al iniciar bot (intento ${reconnectAttempts}):`, err.message);
                
                if (err.message.includes('401') || err.message.includes('404')) {
                    console.error('   → Token de BOT inválido. Regenera el token en @BotFather.');
                    console.error('   → NO se reintentará (error permanente).');
                    break; // No reintentar, el token es malo
                }
                
                if (err.message.includes('409')) {
                    console.error('   → 409 Conflict: otra instancia está usando este token. Limpiando...');
                }
                
                // Para otros errores (red, timeout, 429), reintentar
                if (reconnectAttempts >= 10) {
                    console.error('   → 10 intentos fallidos. Reiniciando proceso Node.js...');
                    process.exit(1); // Docker restart: unless-stopped lo levantará
                }
            }
        }
    }
    
    launchWithAutoReconnect();

    // ─── MANEJO GLOBAL DE ERRORES DE POLLING ───
    // Telegraf puede emitir errores de polling que NO son excepciones normales
    // y si no se capturan, matan el proceso silenciosamente
    bot.catch((err, ctx) => {
        console.error(`❌ [BOT ERROR] Error en handler:`, err.message);
        // NO relanzar — el bot sigue vivo
    });

    process.once('SIGINT', () => bot.stop('SIGINT'));
    process.once('SIGTERM', () => bot.stop('SIGTERM'));
}

// Función para notificar al admin desde otros módulos (ej: index.js para web)
function notifyAdmin(message) {
    if (!botInstance || !ADMIN_IDS.length) return;
    for (const adminId of ADMIN_IDS) {
        botInstance.telegram.sendMessage(adminId, message, { parse_mode: 'HTML' })
            .catch(() => {});
    }
}

// Exportar la función
module.exports = { startBot, notifyAdmin, formatCID };
