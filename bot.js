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
                    ['👥 Usuarios', '⚙️ Ayuda Admin']
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
        const msg = await ctx.reply('🚀 Iniciando Chrome en el servidor... Por favor espera.', { parse_mode: 'Markdown' });
        
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/api/start-renovation`, { method: 'POST' });
            const data = await response.json();
            if (!data.success) {
                ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, `❌ Error: ${data.error}`);
            } else {
                ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, `✅ Proceso iniciado. Si hay CAPTCHA, te enviaré la foto en unos segundos.`);
            }
        } catch (err) {
            ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, `❌ No se pudo contactar al servidor: ${err.message}`);
        }
    });

    bot.hears('📊 Estado Sistema', async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return;
        
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/api/token-status`);
            const data = await response.json();
            
            let msg = `📊 *ESTADO DEL SISTEMA*\n\n`;
            if (data.status === 'valid') msg += `🟢 *Token:* ACTIVO (${data.remaining_minutes} min)\n`;
            else if (data.status === 'expired') msg += `🔴 *Token:* EXPIRADO\n`;
            else msg += `⚪ *Token:* NINGUNO\n`;
            
            const s = db.getStats();
            msg += `👥 *Usuarios:* ${s.totalUsers}\n`;
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
        ctx.reply('Comandos manuales:\n/settoken\n/setrefreshtoken\n/addcredits', { parse_mode: 'Markdown' });
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
    // /settoken — Admin envía token de Microsoft generado localmente
    // ============================================================
    bot.command('settoken', async (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');
        
        const args = ctx.message.text.split(' ').slice(1);
        if (args.length < 1) {
            return ctx.reply(
                '🔑 *Cómo usar /settoken:*\n\n' +
                '1️⃣ En tu PC local, abre `NUEVOGETCID`\n' +
                '2️⃣ Ejecuta: `python scraper.py`\n' +
                '3️⃣ Inicia sesión en Chrome cuando se abra\n' +
                '4️⃣ Copia el token del archivo `ms_token.json`\n' +
                '5️⃣ Envía: `/settoken EL_TOKEN_AQUI`\n\n' +
                '⏱ El token dura ~1 hora.',
                { parse_mode: 'Markdown' }
            );
        }
        
        const token = args.join(' ').trim();
        
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/api/settoken`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token, duration: 3600 })
            });
            
            const data = await response.json();
            
            if (data.success) {
                ctx.reply(`✅ *Token actualizado exitosamente*\n⏱ Válido por 60 minutos.\n\n💡 Usa /tokenstatus para verificar.`, { parse_mode: 'Markdown' });
            } else {
                ctx.reply(`❌ Error: ${data.error}`);
            }
        } catch (err) {
            ctx.reply(`❌ No se pudo conectar con el servicio Python: ${err.message}`);
        }
    });

    // ============================================================
    // /tokenstatus — Verificar estado del token actual
    // ============================================================
    bot.command('tokenstatus', async (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');
        
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/api/token-status`);
            const data = await response.json();
            
            if (data.status === 'valid') {
                ctx.reply(`🟢 *Token ACTIVO*\n⏱ Quedan: ${data.remaining_minutes} minutos`, { parse_mode: 'Markdown' });
            } else if (data.status === 'expired') {
                ctx.reply(`🔴 *Token EXPIRADO*\n\nUsa /settoken para renovarlo.`, { parse_mode: 'Markdown' });
            } else {
                ctx.reply(`⚪ *Sin token*\n\nUsa /settoken para configurar uno.`, { parse_mode: 'Markdown' });
            }
        } catch (err) {
            ctx.reply(`❌ No se pudo verificar: ${err.message}`);
        }
    });

    // ============================================================
    // /setrefreshtoken — Token permanente para auto-renovación
    // ============================================================
    bot.command('setrefreshtoken', async (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');
        
        const text = ctx.message.text.replace('/setrefreshtoken', '').trim();
        
        if (!text) {
            return ctx.reply(
                '🔑 *Cómo usar /setrefreshtoken:*\n\n' +
                '1️⃣ En tu PC, ejecuta el scraper:\n' +
                '`cd NUEVOGETCID && .\\venv\\Scripts\\python.exe scraper.py`\n\n' +
                '2️⃣ Inicia sesión en Chrome\n\n' +
                '3️⃣ Abre `ms_refresh_token.json` y copia TODO el contenido\n\n' +
                '4️⃣ Envía: `/setrefreshtoken {contenido_del_json}`\n\n' +
                '⚠️ *Nota:* Si el token es de tipo SPA, dura máx 24h (el refresh automático lo mantiene activo).\n' +
                '💡 Para tokens de 90 días, usa `/deviceauth`.',
                { parse_mode: 'Markdown' }
            );
        }
        
        try {
            // Intentar parsear como JSON
            const data = JSON.parse(text);
            
            if (!data.refresh_token || !data.client_id) {
                return ctx.reply('❌ JSON inválido. Necesita `refresh_token` y `client_id`.');
            }
            
            const response = await fetch(`${GETCID_SERVICE_URL}/api/setrefreshtoken`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    refresh_token: data.refresh_token,
                    client_id: data.client_id,
                    scopes: data.scopes || ''
                })
            });
            
            const result = await response.json();
            
            if (result.success) {
                // Verificar tipo de token para dar info correcta
                let tokenInfo = '🔄 Auto-renovación activa';
                const clientPrefix = data.client_id.substring(0, 8);
                if (clientPrefix.startsWith('2b217cec')) {
                    tokenInfo = '⚠️ Token SPA (24h máx) — El refresh automático cada 25 min lo mantiene activo';
                } else if (clientPrefix.startsWith('04b07795') || clientPrefix.startsWith('d3590ed6')) {
                    tokenInfo = '✅ Token Native App — Válido por ~90 días con auto-renovación';
                }
                
                ctx.reply(
                    `✅ *Refresh Token configurado*\n\n` +
                    `${tokenInfo}\n` +
                    `🤖 Proactive refresh: cada 25 min\n\n` +
                    `${result.message}`,
                    { parse_mode: 'Markdown' }
                );
            } else {
                ctx.reply(`❌ Error: ${result.error}`);
            }
        } catch (err) {
            if (err instanceof SyntaxError) {
                ctx.reply('❌ El texto no es JSON válido. Copia el contenido COMPLETO del archivo `ms_refresh_token.json`.');
            } else {
                ctx.reply(`❌ Error: ${err.message}`);
            }
        }
    });

    // ============================================================
    // /deviceauth — Iniciar Device Code Flow para token de 90 días
    // ============================================================
    bot.command('deviceauth', async (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');
        
        try {
            const msg = await ctx.reply('🔄 Iniciando Device Code Flow...');
            
            const response = await fetch(`${GETCID_SERVICE_URL}/api/device-auth-start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            const data = await response.json();
            
            if (data.success) {
                await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null,
                    `🔐 *Device Code Flow Activo*\n\n` +
                    `📋 Código: \`${data.user_code}\`\n` +
                    `🌐 URL: ${data.verification_uri}\n` +
                    `🔧 Client: ${data.client_name}\n\n` +
                    `*Pasos:*\n` +
                    `1️⃣ Abre el link de arriba\n` +
                    `2️⃣ Pega el código \`${data.user_code}\`\n` +
                    `3️⃣ Inicia sesión con tu cuenta Microsoft\n\n` +
                    `⏱ Expira en ${Math.floor(data.expires_in / 60)} minutos\n` +
                    `🤖 El servidor capturará el token automáticamente.`,
                    { parse_mode: 'Markdown' }
                );
            } else {
                await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null,
                    `❌ *Device Code Flow falló*\n\n${data.error}`,
                    { parse_mode: 'Markdown' }
                );
            }
        } catch (err) {
            ctx.reply(`❌ Error: ${err.message}`);
        }
    });

    // ============================================================
    // /systemstatus — Estado completo del sistema
    // ============================================================
    bot.command('systemstatus', async (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');
        
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/api/system-status`);
            const data = await response.json();
            
            // Access Token
            let accessInfo = '❓ Desconocido';
            const at = data.access_token || {};
            if (at.status === 'valid') accessInfo = `🟢 Válido (${at.remaining_minutes || '?'} min)`;
            else if (at.status === 'expired') accessInfo = '🔴 Expirado';
            else if (at.status === 'no_token') accessInfo = '⚪ Sin token';
            
            // Refresh Token
            let refreshInfo = '❓ Desconocido';
            const rt = data.refresh_token || {};
            if (rt.status === 'valid') {
                if (rt.token_type === 'spa') {
                    refreshInfo = `🟡 SPA (${rt.remaining_hours || '?'}h restantes)`;
                } else {
                    refreshInfo = `🟢 ${rt.token_type_label || 'OK'} (${rt.remaining_days || '?'} días)`;
                }
            } else if (rt.status === 'expired') refreshInfo = '🔴 EXPIRADO';
            else if (rt.status === 'no_token') refreshInfo = '⚪ No configurado';
            
            // Proactive Refresher
            let refresherInfo = '❌ Inactivo';
            const pr = data.proactive_refresher || {};
            if (pr.running) {
                refresherInfo = `✅ Activo (${pr.total_refreshes || 0} refreshes, ${pr.consecutive_failures || 0} fallos)`;
                if (pr.last_refresh_ago_min !== null) refresherInfo += `\n   Último: hace ${pr.last_refresh_ago_min} min`;
            }
            
            const stats = db.getStats();
            
            ctx.reply(
                `📊 *Estado del Sistema GetCID*\n` +
                `📅 ${new Date().toLocaleString('es-PE')}\n\n` +
                `🔑 Access Token: ${accessInfo}\n` +
                `🔄 Refresh Token: ${refreshInfo}\n` +
                `⚙️ Proactive Refresh: ${refresherInfo}\n\n` +
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

    // ============================================================
    // CAPTCHA INTERACTIVO: Manejador de clics en la botonera [ 0 ]...[ 5 ]
    // ============================================================
    bot.action(/solve_captcha_(\d+)/, async (ctx) => {
        const clicks = parseInt(ctx.match[1]);
        await ctx.answerCbQuery(`Enviando ${clicks} clics...`);
        
        try {
            await ctx.editMessageReplyMarkup({ inline_keyboard: [] }); // Ocultar botones para no hacer doble clic
            
            const response = await fetch(`${GETCID_SERVICE_URL}/api/solve-captcha`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ clicks })
            });
            
            const data = await response.json();
            if (data.success) {
                ctx.reply(`⚙️ Recibido. Procesando ${clicks} clics y esperando validación de Microsoft...\n\n⏳ _Esto puede tomar 1 a 2 minutos, no toques nada más._`, { parse_mode: 'Markdown' });
            } else {
                ctx.reply(`❌ Error en el backend: ${data.error}`);
            }
        } catch (err) {
            ctx.reply(`❌ No se pudo contactar al servidor: ${err.message}`);
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
                    const sysResp = await fetch(`${GETCID_SERVICE_URL}/api/system-status`);
                    const sysData = await sysResp.json();
                    
                    // Access token
                    const at = sysData.access_token || {};
                    if (at.status === 'valid') tokenStatus = `🟢 Válido (${at.remaining_minutes} min)`;
                    else if (at.status === 'expired') tokenStatus = '🔴 Expirado';
                    else tokenStatus = '⚪ Sin token';
                    
                    // Refresh token (con tipo real)
                    const rt = sysData.refresh_token || {};
                    if (rt.status === 'valid') {
                        if (rt.token_type === 'spa') {
                            refreshStatus = `🟡 SPA (refresh hace ${rt.hours_since_last_refresh || '?'}h)`;
                        } else {
                            refreshStatus = `🟢 ${rt.token_type_label || 'OK'} (${rt.remaining_days || '?'} días)`;
                        }
                    } else if (rt.status === 'expired') refreshStatus = '🔴 EXPIRADO - Renovar!';
                    else refreshStatus = '⚪ No configurado';
                    
                    // Proactive refresher
                    const pr = sysData.proactive_refresher || {};
                    if (pr.running) {
                        refresherStatus = `✅ (${pr.total_refreshes} OK, ${pr.consecutive_failures} fallos)`;
                    }
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
