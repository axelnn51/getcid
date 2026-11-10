const { Telegraf, Markup } = require('telegraf');
const fs = require('fs');
const path = require('path');
const { getConfirmationID, CIDError } = require('./cid_helper');
const { ocrAndGetCID } = require('./ocr');
const db = require('./db');

const BOT_TOKEN = process.env.BOT_TOKEN || '8334632533:AAEMCDWK-4sMpmDSSquc5Afz6FRVZjrs6go';
const ADMIN_IDS = (process.env.ADMIN_IDS || '7233007906').split(',').map(s => s.trim()).filter(Boolean);
const GETCID_SERVICE_URL = process.env.GETCID_SERVICE_URL || 'http://getcid_backend:8000';

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
                return `⚠️ <b>Límite de activación por lote (0xD6)</b>\nEsta clave alcanzó el límite de activación por lotes de Microsoft y requiere activación telefónica individual (1 PC).${iidBlock}`;
            
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

    // Estado temporal para carga interactiva de keys por ENTER (solo admin)
    const pendingStockUpload = new Map();
    const STOCK_UPLOAD_TTL_MS = 5 * 60 * 1000;

    function escapeHtml(text) {
        return String(text ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function parseManualOrderArgs(args, defaultCredits = 1) {
        let credits = defaultCredits;
        let customOrderId = null;
        let productName = '';

        if (args.length >= 1) {
            const parsed = parseInt(args[0], 10);
            if (!isNaN(parsed) && parsed > 0) {
                credits = parsed;
                if (args.length >= 2) {
                    if (args[1].toUpperCase().startsWith('CDK-') || /^\d+$/.test(args[1])) {
                        customOrderId = args[1].trim();
                        if (args.length >= 3) productName = args.slice(2).join(' ').trim();
                    } else {
                        productName = args.slice(1).join(' ').trim();
                    }
                }
            } else if (args[0].toUpperCase().startsWith('CDK-')) {
                customOrderId = args[0].trim();
                if (args.length >= 2) productName = args.slice(1).join(' ').trim();
            } else {
                productName = args.join(' ').trim();
            }
        }

        return { credits, customOrderId, productName };
    }

    function buildManualOrderReply({ orderId, credits, productName = '', keys = [], presetCode = null, stockRemaining = null }) {
        const baseLines =
            `📦 N° de Pedido: ${orderId}\n` +
            `💰 Créditos CID: ${credits}\n` +
            `🌐 Portal Web CID: https://getcid.cdkeysperu.com/\n` +
            `🛒 Producto: ${productName}`;

        let clientText = baseLines;
        if (presetCode) {
            if (keys.length === 1) {
                clientText += `\n🔑 Key: ${keys[0]}`;
            } else if (keys.length > 1) {
                clientText += `\n🔑 Keys:\n${keys.join('\n')}`;
            } else {
                // Sin stock: salto de línea listo para pegar/escribir la key manualmente
                clientText += `\n`;
            }
        }

        let stockStatusLine = '';
        if (presetCode) {
            if (keys.length === credits) {
                const lowWarn = stockRemaining === 0 ? ' ⚠️ <i>(¡Última key! Stock en 0)</i>' : '';
                stockStatusLine = `🗝️ <b>Stock (/pedido${escapeHtml(presetCode)}):</b> ${keys.length} asignada(s) · Restantes: <b>${stockRemaining}</b>${lowWarn}\n`;
            } else if (keys.length > 0) {
                const missing = credits - keys.length;
                stockStatusLine = `⚠️ <b>Stock parcial (/pedido${escapeHtml(presetCode)}):</b> se asignaron <b>${keys.length}/${credits}</b> (coloca ${missing} manual). Restantes: <b>0</b>\n`;
            } else {
                stockStatusLine = `⚠️ <b>Sin stock local (/pedido${escapeHtml(presetCode)}):</b> coloca la key manualmente.\n`;
            }
        }

        let bodyWhatsApp =
            `📦 N° de Pedido: <code>${escapeHtml(orderId)}</code>\n` +
            `💰 Créditos CID: ${credits}\n` +
            `🌐 Portal Web CID: https://getcid.cdkeysperu.com/\n` +
            `🛒 Producto: ${escapeHtml(productName)}`;

        if (presetCode && keys.length === 1) {
            bodyWhatsApp += `\n🔑 Key: <code>${escapeHtml(keys[0])}</code>`;
        } else if (presetCode && keys.length > 1) {
            bodyWhatsApp += `\n🔑 Keys:\n<code>${escapeHtml(keys.join('\n'))}</code>`;
        }

        const headerProduct = productName
            ? `🛒 <b>Producto:</b> ${escapeHtml(productName)}\n`
            : '';

        const text =
            `✅ <b>¡PEDIDO GENERADO CON ÉXITO!</b>\n\n` +
            `📦 <b>N° de Pedido:</b> <code>${escapeHtml(orderId)}</code>\n` +
            `💰 <b>Créditos CID:</b> <b>${credits}</b>\n` +
            `🌐 <b>Portal Web CID:</b> https://getcid.cdkeysperu.com/\n` +
            headerProduct +
            stockStatusLine +
            `📅 <b>Fecha:</b> ${new Date().toLocaleString('es-PE')}\n\n` +
            `📋 <b>Texto para WhatsApp:</b>\n` +
            bodyWhatsApp;

        const inlineKeyboard = [];
        // Telegram Bot API limita copy_text.text a máximo 256 caracteres
        if (clientText.length <= 256) {
            inlineKeyboard.push([{
                text: '📋 Copiar Formato WhatsApp (1 Clic)',
                copy_text: { text: clientText }
            }]);
        } else {
            const orderOnly = `${baseLines}\n`;
            const keysOnly = keys.join('\n');
            if (orderOnly.length <= 256) {
                inlineKeyboard.push([{
                    text: '📋 Copiar Datos del Pedido (1 Clic)',
                    copy_text: { text: orderOnly }
                }]);
            }
            if (keysOnly && keysOnly.length <= 256) {
                inlineKeyboard.push([{
                    text: `🔑 Copiar ${keys.length} Keys (1 Clic)`,
                    copy_text: { text: keysOnly }
                }]);
            }
        }

        if (presetCode && stockRemaining === 0) {
            inlineKeyboard.push([
                Markup.button.callback(`📥 Cargar Stock (${presetCode})`, `load_stock_${presetCode}`),
                Markup.button.callback('🗄️ Ver Stock', 'open_stock')
            ]);
        }

        return {
            text,
            extra: {
                parse_mode: 'HTML',
                disable_web_page_preview: true,
                ...(inlineKeyboard.length > 0 ? { reply_markup: { inline_keyboard: inlineKeyboard } } : {})
            }
        };
    }

    function sendPresetOrder(ctx, preset, rawArgs = '') {
        const args = rawArgs ? rawArgs.trim().split(/\s+/).filter(Boolean) : [];
        const parsed = parseManualOrderArgs(args, preset.default_credits || 1);
        const order = db.createManualOrder(parsed.credits, parsed.customOrderId);
        const popped = db.popKeysFromStock(preset.code, parsed.credits, order.orderId);

        const reply = buildManualOrderReply({
            orderId: order.orderId,
            credits: parsed.credits,
            productName: preset.product_name,
            keys: popped.keys,
            presetCode: preset.code,
            stockRemaining: popped.remaining
        });

        return ctx.reply(reply.text, reply.extra);
    }

    function buildStockOverviewPayload() {
        const presets = db.getAllProductPresetsWithStock();
        if (!presets || presets.length === 0) {
            return {
                text: '🗄️ <b>No hay productos configurados.</b>\nUsa <code>/addprod 24 Office Pro Plus LTSC 2024</code>',
                extra: { parse_mode: 'HTML' }
            };
        }

        let text = '🗄️ <b>STOCK LOCAL DE KEYS (FIFO)</b>\n\n';
        const buttons = [];

        for (const p of presets) {
            const badge = p.available_count > 0 ? `🟢 <b>${p.available_count} disp.</b>` : `🔴 <b>0 disp.</b>`;
            text += `• <code>/pedido${escapeHtml(p.code)}</code> — <b>${escapeHtml(p.product_name)}</b>\n`;
            text += `  Stock: ${badge} | Usadas: ${p.used_count} | Cargar: <code>/cargar${escapeHtml(p.code)}</code>\n\n`;
            buttons.push([
                Markup.button.callback(`📥 Cargar ${p.code} (${p.available_count})`, `load_stock_${p.code}`),
                Markup.button.callback(`👁️ Ver Cola ${p.code}`, `view_stock_${p.code}`)
            ]);
        }

        text += '💡 <i>Cada ENTER al cargar cuenta como 1 key y se entregan en orden FIFO.</i>\n';
        text += '➕ <i>Nuevo producto:</i> <code>/addprod &lt;cod&gt; &lt;Nombre&gt;</code>';

        return {
            text,
            extra: {
                parse_mode: 'HTML',
                ...Markup.inlineKeyboard(buttons)
            }
        };
    }

    function buildStockQueuePayload(preset) {
        const queue = db.getStockQueue(preset.code, 15);
        let text = `📦 <b>Cola FIFO — ${escapeHtml(preset.product_name)}</b> (<code>/pedido${escapeHtml(preset.code)}</code>)\n\n`;
        text += `🟢 Disponibles: <b>${queue.available}</b> | ✅ Entregadas: <b>${queue.used}</b>\n\n`;

        if (queue.nextKeys.length === 0) {
            text += '<i>No hay keys disponibles en este producto.</i>\n';
        } else {
            text += `<b>Próximas en salir (en orden):</b>\n`;
            queue.nextKeys.forEach((k, idx) => {
                text += `${idx + 1}. <code>${escapeHtml(k)}</code>\n`;
            });
            if (queue.available > queue.nextKeys.length) {
                text += `<i>... y ${queue.available - queue.nextKeys.length} más en cola.</i>\n`;
            }
        }

        const row1 = [
            Markup.button.callback(`📥 Cargar ${preset.code}`, `load_stock_${preset.code}`),
            Markup.button.callback(`⚡ Generar /pedido${preset.code}`, `gen_preset_${preset.code}`)
        ];
        const rows = [row1];
        if (queue.available > 0) {
            rows.push([
                Markup.button.callback(`🗑️ Vaciar disponibles (${queue.available})`, `ask_clear_stock_${preset.code}`),
                Markup.button.callback('🔙 Todo el Stock', 'open_stock')
            ]);
        } else {
            rows.push([Markup.button.callback('🔙 Todo el Stock', 'open_stock')]);
        }

        return {
            text,
            extra: {
                parse_mode: 'HTML',
                ...Markup.inlineKeyboard(rows)
            }
        };
    }

    function startPendingStockUpload(ctx, preset) {
        const tgId = String(ctx.from.id);
        pendingStockUpload.set(tgId, {
            code: preset.code,
            productName: preset.product_name,
            expiresAt: Date.now() + STOCK_UPLOAD_TTL_MS
        });

        const currentQueue = db.getStockQueue(preset.code, 1);
        return ctx.reply(
            `📥 <b>Cargar Keys — ${escapeHtml(preset.product_name)}</b> (<code>/pedido${escapeHtml(preset.code)}</code>)\n\n` +
            `Stock actual disponible: <b>${currentQueue.available}</b>\n\n` +
            `Envía ahora tus keys en un solo mensaje (<b>cada ENTER cuenta como 1 key</b>, se entregarán en ese orden exacto):\n` +
            `<code>KEY-1\nKEY-2\nKEY-3</code>`,
            {
                parse_mode: 'HTML',
                ...Markup.inlineKeyboard([[Markup.button.callback('❌ Cancelar carga', 'cancel_stock_upload')]])
            }
        );
    }

    // Limpiar sesión de carga si el admin ejecuta cualquier comando "/"
    bot.use((ctx, next) => {
        if (ctx.message?.text?.startsWith('/')) {
            const tgId = String(ctx.from?.id || '');
            if (tgId) pendingStockUpload.delete(tgId);
        }
        return next();
    });

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
                    ['📊 Estado Sistema', '👥 Usuarios'],
                    ['🎟️ Generar Pedido', '⚙️ Ayuda Admin']
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
    bot.hears('📊 Estado Sistema', async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return;
        
        try {
            const response = await fetch(`${GETCID_SERVICE_URL}/status`);
            const data = await response.json();
            
            let msg = `📊 *ESTADO DEL SISTEMA*\n\n`;
            msg += `🔌 *API:* ${data.api_status === 'online' ? '🟢 Online' : '🔴 Offline'}\n`;
            msg += `⚙️ *Versión:* ${data.version || '3.0'}\n`;
            msg += `🚀 *Motor:* ${data.engine || 'Batch API'}\n`;
            
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

    bot.hears('🎟️ Generar Pedido', (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return;

        const presets = db.getAllProductPresetsWithStock();
        const presetButtons = presets.slice(0, 4).map(p =>
            Markup.button.callback(`🏷️ /pedido${p.code} (${p.available_count})`, `gen_preset_${p.code}`)
        );

        const keyboardRows = [
            [
                Markup.button.callback('⚡ 1 Crédito', 'gen_order_1'),
                Markup.button.callback('⚡ 2 Créditos', 'gen_order_2'),
                Markup.button.callback('⚡ 3 Créditos', 'gen_order_3')
            ],
            [
                Markup.button.callback('🌟 5 Créditos', 'gen_order_5'),
                Markup.button.callback('💎 10 Créditos', 'gen_order_10'),
                Markup.button.callback('📋 Ver Pedidos', 'list_orders')
            ]
        ];

        for (let i = 0; i < presetButtons.length; i += 2) {
            keyboardRows.push(presetButtons.slice(i, i + 2));
        }
        keyboardRows.push([Markup.button.callback('🗄️ Gestionar Stock Local', 'open_stock')]);

        ctx.reply(
            '🎟️ <b>Generar Pedido con Créditos (Para ventas externas/WhatsApp)</b>\n\n' +
            'Selecciona cuántos créditos CID o un producto rápido con stock FIFO:\n\n' +
            '💡 <i>Atajos:</i> <code>/pedido21</code>, <code>/pedido24</code> o <code>/pedido &lt;créditos&gt;</code>',
            {
                parse_mode: 'HTML',
                ...Markup.inlineKeyboard(keyboardRows)
            }
        );
    });

    // Callback handlers para los botones de generar pedido
    bot.action(/^gen_order_(\d+)$/, async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.answerCbQuery('❌ No autorizado');

        const credits = parseInt(ctx.match[1], 10);
        const order = db.createManualOrder(credits);

        await ctx.answerCbQuery(`¡Pedido #${order.orderId} creado con ${credits} crédito(s)!`);

        const reply = buildManualOrderReply({
            orderId: order.orderId,
            credits,
            productName: ''
        });
        ctx.reply(reply.text, reply.extra);
    });

    bot.action(/^gen_preset_([a-z0-9_-]+)$/i, async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.answerCbQuery('❌ No autorizado');
        const preset = db.getProductPreset(ctx.match[1]);
        if (!preset) return ctx.answerCbQuery('❌ Producto no encontrado');
        await ctx.answerCbQuery(`Generando /pedido${preset.code}...`);
        return sendPresetOrder(ctx, preset, '');
    });

    bot.action('open_stock', async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.answerCbQuery('❌ No autorizado');
        await ctx.answerCbQuery();
        const payload = buildStockOverviewPayload();
        return ctx.reply(payload.text, payload.extra);
    });

    bot.action(/^view_stock_([a-z0-9_-]+)$/i, async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.answerCbQuery('❌ No autorizado');
        const preset = db.getProductPreset(ctx.match[1]);
        if (!preset) return ctx.answerCbQuery('❌ Producto no encontrado');
        await ctx.answerCbQuery();
        const payload = buildStockQueuePayload(preset);
        return ctx.reply(payload.text, payload.extra);
    });

    bot.action(/^load_stock_([a-z0-9_-]+)$/i, async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.answerCbQuery('❌ No autorizado');
        const preset = db.getProductPreset(ctx.match[1]);
        if (!preset) return ctx.answerCbQuery('❌ Producto no encontrado');
        await ctx.answerCbQuery();
        return startPendingStockUpload(ctx, preset);
    });

    bot.action(/^ask_clear_stock_([a-z0-9_-]+)$/i, async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.answerCbQuery('❌ No autorizado');
        const preset = db.getProductPreset(ctx.match[1]);
        if (!preset) return ctx.answerCbQuery('❌ Producto no encontrado');
        await ctx.answerCbQuery();
        return ctx.reply(
            `⚠️ ¿Seguro que deseas vaciar todas las keys disponibles de <b>${escapeHtml(preset.product_name)}</b> (<code>/pedido${escapeHtml(preset.code)}</code>)?`,
            {
                parse_mode: 'HTML',
                ...Markup.inlineKeyboard([[
                    Markup.button.callback('🗑️ Sí, vaciar disponibles', `confirm_clear_stock_${preset.code}`),
                    Markup.button.callback('❌ Cancelar', 'cancel_stock_upload')
                ]])
            }
        );
    });

    bot.action(/^confirm_clear_stock_([a-z0-9_-]+)$/i, async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.answerCbQuery('❌ No autorizado');
        const preset = db.getProductPreset(ctx.match[1]);
        if (!preset) return ctx.answerCbQuery('❌ Producto no encontrado');
        const removed = db.clearAvailableStock(preset.code);
        await ctx.answerCbQuery(`Vaciadas ${removed} keys`);
        return ctx.reply(`🗑️ Se eliminaron <b>${removed}</b> key(s) disponibles de <b>${escapeHtml(preset.product_name)}</b>.`, { parse_mode: 'HTML' });
    });

    bot.action('cancel_stock_upload', async (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.answerCbQuery('❌ No autorizado');
        pendingStockUpload.delete(String(ctx.from.id));
        await ctx.answerCbQuery('Cancelado');
        return ctx.reply('✅ Operación cancelada.', { parse_mode: 'HTML' });
    });

    bot.action('list_orders', (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.answerCbQuery('❌ No autorizado');
        ctx.answerCbQuery();
        
        const orders = db.getManualOrders(10);
        if (!orders || orders.length === 0) {
            return ctx.reply('📦 No hay pedidos registrados aún.');
        }
        
        let msg = '📋 <b>Últimos Pedidos Registrados:</b>\n\n';
        orders.forEach(o => {
            const status = o.remaining > 0 ? `🟢 <b>${o.remaining}/${o.total_credits} disponibles</b>` : `🔴 Agotado (${o.used_credits}/${o.total_credits})`;
            msg += `📦 <code>#${o.order_id}</code> — ${status}\n`;
        });
        msg += '\n💡 <i>Usa /pedido &lt;créditos&gt; para generar uno nuevo al instante.</i>';
        ctx.reply(msg, { parse_mode: 'HTML' });
    });

    bot.hears('⚙️ Ayuda Admin', (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return;
        ctx.reply(
            '⚙️ *Comandos Admin Disponibles:*\n\n' +
            '🎟️ *Generar Pedido* — Crear pedido aleatorio con créditos o producto rápido\n' +
            '📊 *Estado Sistema* — Resumen rápido del servidor\n' +
            '👥 *Usuarios* — Ver usuarios actuales\n\n' +
            '*Pedidos y Stock Local (FIFO):*\n' +
            '/pedido24 [n] — Office Pro Plus LTSC 2024 + key de stock\n' +
            '/pedido21 [n] — Office Pro Plus 2021 + key de stock\n' +
            '/pedido [n] — Pedido genérico con producto manual\n' +
            '/cargar24 o /cargar21 — Cargar keys (1 por línea/ENTER)\n' +
            '/stock — Ver stock restante y colas FIFO\n' +
            '/addprod <cod> <Nombre> — Crear nuevo atajo /pedido<cod>\n' +
            '/pedidos — Listar últimos pedidos y créditos\n\n' +
            '*Sistema:*\n' +
            '/systemstatus — Estado completo del servidor\n' +
            '/addcredits <id> <n> — Agregar créditos a un usuario\n' +
            '/stats — Estadísticas globales',
            { parse_mode: 'Markdown' }
        );
    });

    bot.command('balance', (ctx) => {
        const tgId = String(ctx.from.id);
        const user = db.findUserByTelegram(tgId);
        if (!user) return ctx.reply('Usa /start primero.');
        if (isAdmin(tgId)) {
            return ctx.reply('💰 Balance: <b>👑 Ilimitado (Admin)</b>', { parse_mode: 'HTML' });
        }
        ctx.reply(`💰 Balance: *${user.balance} CIDs*`, { parse_mode: 'Markdown' });
    });

    bot.command(['help', 'ayuda'], (ctx) => {
        const tgId = String(ctx.from.id);
        const admin = isAdmin(tgId);
        let msg = `👋 <b>Asistente de Activación CID — CDKeys Perú</b>\n\n` +
            `📸 <b>Por Foto:</b> Envíame una foto clara de la pantalla de activación (OCR automático).\n` +
            `📝 <b>Por Texto:</b> Escribe o pega los 63 dígitos del Installation ID (IID).\n` +
            `⚡ <b>Comando:</b> <code>/cid &lt;IID&gt;</code>\n` +
            `💰 <b>Tu Saldo:</b> /balance\n` +
            `🔑 <b>Verificar Clave:</b> <code>/check &lt;XXXXX-XXXXX-XXXXX-XXXXX-XXXXX&gt;</code>\n\n`;
        if (admin) {
            msg += `👑 <b>Comandos de Administrador:</b>\n` +
                `🏷️ <code>/pedido24 [n]</code> — Office LTSC 2024 (+ key FIFO)\n` +
                `🏷️ <code>/pedido21 [n]</code> — Office 2021 (+ key FIFO)\n` +
                `🎟️ <code>/pedido [n]</code> — Crear pedido genérico para WhatsApp\n` +
                `📥 <code>/cargar24</code> / <code>/cargar21</code> — Cargar keys por ENTER\n` +
                `🗄️ <code>/stock</code> — Ver y administrar colas de keys\n` +
                `➕ <code>/addprod &lt;cod&gt; &lt;Nombre&gt;</code> — Nuevo atajo de producto\n` +
                `📋 <code>/pedidos</code> — Ver últimos pedidos y saldo restante\n` +
                `➕ <code>/addcredits &lt;id&gt; &lt;n&gt;</code> — Asignar créditos a usuario Telegram\n` +
                `📊 <code>/stats</code> — Estadísticas globales\n` +
                `⚙️ <code>/systemstatus</code> — Estado del servidor y motor Batch\n` +
                `🔄 <code>/restart</code> — Reiniciar el bot\n`;
        }
        ctx.reply(msg, { parse_mode: 'HTML' });
    });

    bot.command(['cid', 'activar', 'getcid'], async (ctx) => {
        const text = ctx.message.text || '';
        const parts = text.split(/\s+/).slice(1).join(' ').trim();
        const digits = parts.replace(/\D/g, '');
        
        if (digits.length >= 54) {
            return await handleDirectIID(ctx, parts);
        }
        
        return ctx.reply(
            '🔑 <b>Obtención de Confirmation ID (CID)</b>\n\n' +
            'Uso: <code>/cid &lt;IID de 63 dígitos&gt;</code>\n\n' +
            '💡 <i>Ejemplo:</i>\n' +
            '<code>/cid 3653063 3391384 3605555 1079123 5278526 9415510 9640786 1735154 8663603</code>\n\n' +
            '📸 <i>También puedes enviarme directamente la captura o escribir los 63 dígitos sin ningún comando.</i>',
            { parse_mode: 'HTML' }
        );
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

    bot.command(['pedido', 'generarpedido', 'crearpedido'], (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');

        const args = ctx.message.text.split(/\s+/).slice(1).filter(Boolean);
        const { credits, customOrderId, productName } = parseManualOrderArgs(args, 1);
        const order = db.createManualOrder(credits, customOrderId);

        const reply = buildManualOrderReply({
            orderId: order.orderId,
            credits,
            productName
        });
        ctx.reply(reply.text, reply.extra);
    });

    bot.command(['pedidos', 'listarpedidos'], (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');
        
        const orders = db.getManualOrders(12);
        if (!orders || orders.length === 0) {
            return ctx.reply('📦 No hay pedidos registrados aún.');
        }
        
        let msg = '📋 <b>Últimos Pedidos Registrados:</b>\n\n';
        orders.forEach(o => {
            const status = o.remaining > 0 ? `🟢 <b>${o.remaining}/${o.total_credits} disponibles</b>` : `🔴 Agotado (${o.used_credits}/${o.total_credits})`;
            msg += `📦 <code>#${o.order_id}</code> — ${status}\n`;
        });
        msg += '\n💡 <i>Usa /pedido &lt;créditos&gt; para crear uno nuevo.</i>';
        ctx.reply(msg, { parse_mode: 'HTML' });
    });

    // Atajos rápidos dinámicos: /pedido24, /pedido21, etc. (+ consumo FIFO de stock local)
    bot.hears(/^\/pedido([a-z0-9_-]+)(?:@\w+)?(?:\s+(.*))?$/i, (ctx, next) => {
        const code = String(ctx.match[1] || '').toLowerCase();
        if (code === 's') return next();

        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');

        const preset = db.getProductPreset(code);
        if (!preset) {
            const all = db.getAllProductPresetsWithStock();
            const availableList = all.map(p => `<code>/pedido${escapeHtml(p.code)}</code> (${escapeHtml(p.product_name)})`).join('\n') || 'Ninguno';
            return ctx.reply(
                `⚠️ No existe el producto rápido <code>/pedido${escapeHtml(code)}</code>.\n\n` +
                `<b>Disponibles:</b>\n${availableList}\n\n` +
                `💡 Crea uno nuevo con:\n<code>/addprod ${escapeHtml(code)} Nombre del Producto</code>`,
                { parse_mode: 'HTML' }
            );
        }

        return sendPresetOrder(ctx, preset, ctx.match[2] || '');
    });

    // Carga directa de keys: /cargar24, /cargar21 (solo o con bloque multilínea)
    bot.hears(/^\/cargar([a-z0-9_-]+)(?:@\w+)?(?:\s+([\s\S]*))?$/i, (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');

        const code = String(ctx.match[1] || '').toLowerCase();
        const preset = db.getProductPreset(code);
        if (!preset) {
            return ctx.reply(
                `⚠️ No existe el producto <code>${escapeHtml(code)}</code>.\n` +
                `Primero créalo con <code>/addprod ${escapeHtml(code)} Nombre del Producto</code> o revisa /stock.`,
                { parse_mode: 'HTML' }
            );
        }

        const rawPayload = (ctx.match[2] || '').trim();
        if (rawPayload) {
            const { added, remaining } = db.addKeysToStock(preset.code, rawPayload);
            return ctx.reply(
                `✅ Se agregaron <b>${added}</b> key(s) a <b>${escapeHtml(preset.product_name)}</b> (<code>/pedido${escapeHtml(preset.code)}</code>).\n` +
                `📦 Stock disponible ahora: <b>${remaining}</b>`,
                {
                    parse_mode: 'HTML',
                    ...Markup.inlineKeyboard([[Markup.button.callback(`👁️ Ver Cola ${preset.code}`, `view_stock_${preset.code}`)]])
                }
            );
        }

        return startPendingStockUpload(ctx, preset);
    });

    bot.command('cargar', (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');

        const m = ctx.message.text.match(/^\/cargar(?:@\w+)?(?:\s+([a-z0-9_-]+))?(?:\s+([\s\S]*))?$/i);
        const code = m?.[1]?.toLowerCase();
        if (!code) {
            const payload = buildStockOverviewPayload();
            return ctx.reply(
                `📥 <b>¿A qué producto deseas cargar keys?</b>\n\nUso rápido: <code>/cargar24</code> o <code>/cargar21</code> (luego pegas 1 key por línea / ENTER).\n\n${payload.text}`,
                payload.extra
            );
        }

        const preset = db.getProductPreset(code);
        if (!preset) {
            return ctx.reply(`⚠️ Producto <code>${escapeHtml(code)}</code> no encontrado. Usa /stock para ver los disponibles.`, { parse_mode: 'HTML' });
        }

        const rawPayload = (m?.[2] || '').trim();
        if (rawPayload) {
            const { added, remaining } = db.addKeysToStock(preset.code, rawPayload);
            return ctx.reply(
                `✅ Se agregaron <b>${added}</b> key(s) a <b>${escapeHtml(preset.product_name)}</b> (<code>/pedido${escapeHtml(preset.code)}</code>).\n` +
                `📦 Stock disponible ahora: <b>${remaining}</b>`,
                {
                    parse_mode: 'HTML',
                    ...Markup.inlineKeyboard([[Markup.button.callback(`👁️ Ver Cola ${preset.code}`, `view_stock_${preset.code}`)]])
                }
            );
        }

        return startPendingStockUpload(ctx, preset);
    });

    bot.hears(/^\/stock([a-z0-9_-]+)(?:@\w+)?$/i, (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');

        const preset = db.getProductPreset(ctx.match[1]);
        if (!preset) {
            return ctx.reply(`⚠️ Producto <code>${escapeHtml(ctx.match[1])}</code> no encontrado. Usa /stock para ver la lista.`, { parse_mode: 'HTML' });
        }

        const payload = buildStockQueuePayload(preset);
        return ctx.reply(payload.text, payload.extra);
    });

    bot.command(['stock', 'productos'], (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');

        const args = ctx.message.text.split(/\s+/).slice(1).filter(Boolean);
        if (args.length > 0) {
            const preset = db.getProductPreset(args[0]);
            if (!preset) {
                return ctx.reply(`⚠️ Producto <code>${escapeHtml(args[0])}</code> no encontrado.`, { parse_mode: 'HTML' });
            }
            const payload = buildStockQueuePayload(preset);
            return ctx.reply(payload.text, payload.extra);
        }

        const payload = buildStockOverviewPayload();
        return ctx.reply(payload.text, payload.extra);
    });

    bot.command('addprod', (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');

        const m = ctx.message.text.match(/^\/addprod(?:@\w+)?\s+([a-z0-9_-]+)\s+(.+)$/i);
        if (!m) {
            return ctx.reply(
                '➕ <b>Crear / Editar Producto Rápido</b>\n\n' +
                'Uso: <code>/addprod &lt;codigo&gt; &lt;Nombre del Producto&gt;</code>\n\n' +
                '💡 Ejemplo:\n<code>/addprod 11 Windows 11 Pro</code>\n' +
                'Luego podrás usar <code>/pedido11</code>, <code>/cargar11</code> y <code>/stock11</code>.',
                { parse_mode: 'HTML' }
            );
        }

        try {
            const preset = db.upsertProductPreset(m[1], m[2], 1);
            return ctx.reply(
                `✅ <b>Producto configurado:</b>\n\n` +
                `• Comando pedido: <code>/pedido${escapeHtml(preset.code)}</code>\n` +
                `• Producto: <b>${escapeHtml(preset.product_name)}</b>\n` +
                `• Cargar stock: <code>/cargar${escapeHtml(preset.code)}</code>\n` +
                `• Ver cola: <code>/stock${escapeHtml(preset.code)}</code>`,
                { parse_mode: 'HTML' }
            );
        } catch (err) {
            return ctx.reply(`❌ ${escapeHtml(err.message)}`, { parse_mode: 'HTML' });
        }
    });

    bot.command('delprod', (ctx) => {
        const tgId = String(ctx.from.id);
        if (!isAdmin(tgId)) return ctx.reply('❌ No tienes permisos de admin.');

        const code = ctx.message.text.split(/\s+/)[1];
        if (!code) {
            return ctx.reply('Uso: <code>/delprod &lt;codigo&gt;</code> (ej: <code>/delprod 11</code>)', { parse_mode: 'HTML' });
        }

        const deleted = db.deleteProductPreset(code);
        return ctx.reply(
            deleted
                ? `🗑️ Producto <code>${escapeHtml(code)}</code> eliminado.`
                : `⚠️ No existe el producto <code>${escapeHtml(code)}</code>.`,
            { parse_mode: 'HTML' }
        );
    });

    bot.command('cancelar', (ctx) => {
        const tgId = String(ctx.from.id);
        pendingStockUpload.delete(tgId);
        return ctx.reply('✅ Operación cancelada.', { parse_mode: 'HTML' });
    });

    bot.command('stats', (ctx) => {
        if (!isAdmin(String(ctx.from.id))) return ctx.reply('❌ No tienes permisos.');
        const s = db.getStats();
        ctx.reply(`📊 Usuarios: ${s.totalUsers}\nCIDs hoy: ${s.todayCids}\nCIDs total: ${s.totalCids}`);
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
                `⚙️ Versión: ${data.version || '3.0'}\n` +
                `🚀 Motor: ${data.engine || 'Batch API'}\n\n` +
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
    // LiveTracker — Actualizador de progreso en tiempo real para Telegram
    // ============================================================
    class LiveTracker {
        constructor(ctx, initialMsg, options = {}) {
            this.ctx = ctx;
            this.msg = initialMsg;
            this.startTime = Date.now();
            this.lastEditTime = 0;
            this.stage = options.initialStage || 'init';
            this.data = options.initialData || {};
            this.timer = null;
            this.stopped = false;
            this.isEditing = false;
            this.renderFn = options.renderFn || null;
            this.lastRenderedText = '';
        }

        getElapsedSecs() {
            return Math.floor((Date.now() - this.startTime) / 1000);
        }

        formatSecs(s) {
            return String(s).padStart(2, '0');
        }

        start(renderFn, intervalMs = 2000) {
            if (renderFn) this.renderFn = renderFn;
            this.timer = setInterval(async () => {
                if (this.stopped) return;
                await this.tick();
            }, intervalMs);
        }

        update(stage, data = {}) {
            this.stage = stage;
            this.data = { ...this.data, ...data };
            if (Date.now() - this.lastEditTime >= 1500) {
                this.tick();
            }
        }

        async tick() {
            if (this.stopped || this.isEditing) return;
            const now = Date.now();
            if (now - this.lastEditTime < 1500) return; // Respetar rate-limit de Telegram

            const text = this.renderFn ? this.renderFn(this) : null;
            if (!text || text === this.lastRenderedText) return;

            this.isEditing = true;
            try {
                await this.ctx.telegram.editMessageText(
                    this.ctx.chat.id,
                    this.msg.message_id,
                    null,
                    text,
                    { parse_mode: 'HTML' }
                );
                this.lastEditTime = Date.now();
                this.lastRenderedText = text;
            } catch (e) {
                // Silenciar errores de rate-limit o "message is not modified"
            } finally {
                this.isEditing = false;
            }
        }

        stop() {
            this.stopped = true;
            if (this.timer) {
                clearInterval(this.timer);
                this.timer = null;
            }
        }
    }

    // ============================================================
    // FOTOS — Con mensaje en tiempo real, contador y desglose
    // ============================================================
    bot.on('photo', async (ctx) => {
        const tgId = String(ctx.from.id);
        const isUserAdmin = isAdmin(tgId);
        let user = db.findUserByTelegram(tgId);
        if (!user) user = db.createUser({ telegram_id: tgId, telegram_username: ctx.from.username });
        if (!isUserAdmin && user.balance <= 0) return ctx.reply('❌ Sin créditos. Contacta al admin.');

        // Rate limit (admins bypass rate limit)
        if (!isUserAdmin) {
            const rl = checkRateLimit(tgId);
            if (rl.limited) return ctx.reply(`⏳ Espera ${rl.waitSecs}s antes de enviar otra solicitud.`);
        }

        const startTime = Date.now();
        const msg = await ctx.reply('⏳ [00s] Descargando imagen de Telegram...', { parse_mode: 'HTML' });

        const tracker = new LiveTracker(ctx, msg, {
            initialStage: 'downloading',
            renderFn: (t) => {
                const s = t.formatSecs(t.getElapsedSecs());
                const iid = t.data.iid;
                const formattedIID = iid ? formatIID(iid) : null;

                if (t.stage === 'downloading') {
                    return `⏳ [${s}s] Descargando imagen de Telegram...`;
                }
                if (t.stage === 'ocr') {
                    return `⚡ [${s}s] Analizando imagen (OCR v3.3 Turbo)...`;
                }
                if (t.stage === 'calling_cid' || t.stage === 'waiting_ms') {
                    let text = `🔍 <b>IID detectado:</b>\n<code>${formattedIID}</code>\n\n`;
                    const elapsed = t.getElapsedSecs();
                    if (elapsed < 6) {
                        text += `🌐 [${s}s] Conectando con Microsoft (Batch API)...`;
                    } else if (elapsed < 12) {
                        text += `🌐 [${s}s] Validando activación con Microsoft...`;
                    } else if (elapsed < 18) {
                        text += `🌐 [${s}s] Esperando confirmación de Microsoft...`;
                    } else {
                        text += `🔄 [${s}s] Reintentando vía Visual API Microsoft...`;
                    }
                    return text;
                }
                if (t.stage === 'rescue') {
                    return `⚠️ [${s}s] Checksum dudoso. Ejecutando escaneo profundo de rescate...`;
                }
                return `⏳ [${s}s] Procesando...`;
            }
        });
        tracker.start(null, 2000);

        try {
            const photo = ctx.message.photo[ctx.message.photo.length - 1];
            const file = await ctx.telegram.getFile(photo.file_id);
            const filePath = path.join(__dirname, 'uploads', `tg_${Date.now()}.jpg`);
            if (!fs.existsSync(path.join(__dirname, 'uploads'))) fs.mkdirSync(path.join(__dirname, 'uploads'), { recursive: true });

            const resp = await fetch(`https://api.telegram.org/file/bot${BOT_TOKEN}/${file.file_path}`);
            fs.writeFileSync(filePath, Buffer.from(await resp.arrayBuffer()));

            tracker.update('ocr');

            const result = await ocrAndGetCID(filePath, getConfirmationID, (stage, data) => {
                if (stage === 'ocr_done') {
                    tracker.update('calling_cid', { iid: data.iid });
                } else if (stage === 'calling_cid' || stage === 'calling_cid_rescue') {
                    tracker.update('calling_cid', { iid: data.iid });
                } else if (stage === 'rescue_mode') {
                    tracker.update('rescue');
                }
            });
            if (fs.existsSync(filePath)) fs.unlinkSync(filePath);

            tracker.stop();

            const elapsed = Date.now() - startTime;
            const secs = (elapsed / 1000).toFixed(0).padStart(2, '0');

            if (result.success) {
                if (!isUserAdmin) db.debitCredit(user.id);
                const bal = isUserAdmin ? '👑 Ilimitado (Admin)' : `${db.getBalance(user.id)} CIDs`;
                const cidStr = formatCID(result.cid);
                db.logTransaction(user.id, 'telegram', result.iid, cidStr, 'success', elapsed, result.strategy);
                
                // Formatear tiempos desglosados
                const ocrSecs = typeof result.ocrElapsed === 'number' ? result.ocrElapsed.toFixed(2) : null;
                const msSecs = typeof result.cidElapsed === 'number' ? result.cidElapsed.toFixed(1) : null;
                let timingStr = `⏱ 00:${secs}`;
                if (ocrSecs && msSecs) {
                    timingStr += ` (⚡ OCR: ${ocrSecs}s | 🌐 MS: ${msSecs}s)`;
                } else if (ocrSecs) {
                    timingStr += ` (⚡ OCR: ${ocrSecs}s)`;
                }

                // Mensaje principal
                await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null,
                    `🔑 <b>@CdKeysPeru</b>\n\n` +
                    `<b>IID:</b>\n<code>${formatIID(result.iid)}</code>\n\n` +
                    `<b>CID:</b>\n<code>${cidStr}</code>\n\n` +
                    `<i>💰 Saldo: ${bal} | ${timingStr}</i>\n` +
                    `<i>🤖 Resuelto vía: ${result.backendMethod || 'Desconocido'} (OCR: ${result.strategy || result.method || 'v3.3 Turbo'})</i>`,
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
            tracker.stop();
            console.error('[BOT PHOTO ERROR]', err.code || err.message);
            const elapsed = Date.now() - startTime;
            db.logTransaction(user.id, 'telegram', err?.iid || null, null, err?.code || 'error', elapsed, null);
            
            const errorMsg = errorToMessage(err, err?.iid);
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, errorMsg, { parse_mode: 'HTML' }).catch(() => {});
        }
    });

    // ============================================================
    // TEXTO (IID directo) — Con contador en tiempo real y progreso
    // ============================================================
    async function handleDirectIID(ctx, rawText) {
        const digits = rawText.replace(/\D/g, '');
        
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
        const isUserAdmin = isAdmin(tgId);
        let user = db.findUserByTelegram(tgId);
        if (!user) user = db.createUser({ telegram_id: tgId, telegram_username: ctx.from.username });
        if (!isUserAdmin && user.balance <= 0) return ctx.reply('❌ Sin créditos. Contacta al admin.');

        // Rate limit (admins bypass rate limit)
        if (!isUserAdmin) {
            const rl = checkRateLimit(tgId);
            if (rl.limited) return ctx.reply(`⏳ Espera ${rl.waitSecs}s antes de enviar otra solicitud.`);
        }

        const startTime = Date.now();
        const msg = await ctx.reply(
            `🔍 <b>IID:</b>\n<code>${formatIID(digits)}</code>\n\n` +
            `🌐 [00s] Conectando con Microsoft (Batch API)...`,
            { parse_mode: 'HTML' }
        );

        const tracker = new LiveTracker(ctx, msg, {
            initialStage: 'waiting_ms',
            initialData: { iid: digits },
            renderFn: (t) => {
                const s = t.formatSecs(t.getElapsedSecs());
                const formattedIID = formatIID(t.data.iid || digits);
                let text = `🔍 <b>IID:</b>\n<code>${formattedIID}</code>\n\n`;
                const elapsed = t.getElapsedSecs();
                if (elapsed < 6) {
                    text += `🌐 [${s}s] Conectando con Microsoft (Batch API)...`;
                } else if (elapsed < 12) {
                    text += `🌐 [${s}s] Validando activación con Microsoft...`;
                } else if (elapsed < 18) {
                    text += `🌐 [${s}s] Esperando confirmación de Microsoft...`;
                } else {
                    text += `🔄 [${s}s] Reintentando vía Visual API Microsoft...`;
                }
                return text;
            }
        });
        tracker.start(null, 2000);

        try {
            const cidResult = await getConfirmationID(digits);
            tracker.stop();

            const cid = typeof cidResult === 'string' ? cidResult : cidResult.cid;
            const backendMethod = typeof cidResult === 'object' ? cidResult.method : 'unknown';
            
            const elapsed = Date.now() - startTime;
            const secs = (elapsed / 1000).toFixed(0).padStart(2, '0');
            if (!isUserAdmin) db.debitCredit(user.id);
            const bal = isUserAdmin ? '👑 Ilimitado (Admin)' : `${db.getBalance(user.id)} CIDs`;
            const cidStr = formatCID(cid);
            db.logTransaction(user.id, 'telegram', digits, cidStr, 'success', elapsed, 'direct');
            
            const msSecs = (elapsed / 1000).toFixed(1);

            // Mensaje principal
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null,
                `🔑 <b>@CdKeysPeru</b>\n\n` +
                `<b>IID:</b>\n<code>${formatIID(digits)}</code>\n\n` +
                `<b>CID:</b>\n<code>${cidStr}</code>\n\n` +
                `<i>💰 Saldo: ${bal} | ⏱ 00:${secs} (🌐 MS: ${msSecs}s)</i>\n` +
                `<i>🤖 Resuelto vía: ${backendMethod}</i>`,
                { parse_mode: 'HTML' }
            );
            
            // CID como texto separado para copiar
            await ctx.reply(`📋 CID para copiar:\n<code>${cidStr.replace(/-/g, '')}</code>`, { parse_mode: 'HTML' });
        } catch (err) {
            tracker.stop();
            const elapsed = Date.now() - startTime;
            db.logTransaction(user.id, 'telegram', digits, null, err?.code || 'error', elapsed, null);
            
            const errorMsg = errorToMessage(err, digits);
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, errorMsg, { parse_mode: 'HTML' });
        }
    }

    bot.on('text', async (ctx) => {
        if (ctx.message.text.startsWith('/')) return;

        const tgId = String(ctx.from.id);
        const pending = pendingStockUpload.get(tgId);
        if (isAdmin(tgId) && pending) {
            if (Date.now() > pending.expiresAt) {
                pendingStockUpload.delete(tgId);
                return ctx.reply(
                    `⏱️ La sesión de carga para <b>${escapeHtml(pending.productName)}</b> expiró. Ejecuta <code>/cargar${escapeHtml(pending.code)}</code> nuevamente.`,
                    { parse_mode: 'HTML' }
                );
            }

            const { added, remaining } = db.addKeysToStock(pending.code, ctx.message.text);
            pendingStockUpload.delete(tgId);

            if (added === 0) {
                return ctx.reply('⚠️ No se detectaron keys válidas. Envía al menos una línea de texto.', { parse_mode: 'HTML' });
            }

            return ctx.reply(
                `✅ Se agregaron <b>${added}</b> key(s) a <b>${escapeHtml(pending.productName)}</b> (<code>/pedido${escapeHtml(pending.code)}</code>).\n` +
                `📦 Stock disponible ahora: <b>${remaining}</b>`,
                {
                    parse_mode: 'HTML',
                    ...Markup.inlineKeyboard([[
                        Markup.button.callback(`👁️ Ver Cola ${pending.code}`, `view_stock_${pending.code}`),
                        Markup.button.callback(`⚡ Generar /pedido${pending.code}`, `gen_preset_${pending.code}`)
                    ]])
                }
            );
        }

        await handleDirectIID(ctx, ctx.message.text);
    });

    // ============================================================
    // HANDLERS DE MENÚ: Estado Token y Device Auth
    // ============================================================
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
                const stats = db.getStats();
                const uptime = Math.floor(process.uptime() / 3600);
                
                for (const adminId of ADMIN_IDS) {
                    bot.telegram.sendMessage(adminId,
                        `📊 *Reporte Diario GetCID*\n` +
                        `📅 ${today} | 3:00 PM\n\n` +
                        `🟢 Sistema 100% Estable (Batch API)\n\n` +
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
        { command: 'pedido24', description: 'Admin: Pedido Office LTSC 2024 + key' },
        { command: 'pedido21', description: 'Admin: Pedido Office 2021 + key' },
        { command: 'pedido', description: 'Admin: Crear pedido genérico' },
        { command: 'cargar24', description: 'Admin: Cargar keys Office 2024' },
        { command: 'cargar21', description: 'Admin: Cargar keys Office 2021' },
        { command: 'stock', description: 'Admin: Ver colas de stock FIFO' },
        { command: 'addprod', description: 'Admin: Nuevo atajo /pedido<cod>' },
        { command: 'pedidos', description: 'Admin: Listar pedidos y saldo' },
        { command: 'addcredits', description: 'Admin: Agregar créditos a usuario' },
        { command: 'systemstatus', description: 'Admin: Ver estado servidor' },
        { command: 'stats', description: 'Admin: Estadísticas globales' },
        { command: 'restart', description: 'Admin: Reiniciar el bot' }
    ]).catch(err => console.log('⚠️ Error configurando comandos:', err.message));



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
                        await bot.telegram.deleteWebhook({ drop_pending_updates: false });
                    } catch (e) { /* ignore */ }
                    
                    const delay = Math.min(5000 * Math.pow(2, reconnectAttempts - 1), MAX_RECONNECT_DELAY);
                    console.log(`🔄 [RECONNECT] Intento ${reconnectAttempts} — esperando ${delay/1000}s...`);
                    await new Promise(r => setTimeout(r, delay));
                }
                
                await bot.launch({ dropPendingUpdates: false });
                
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
                        `/systemstatus`,
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
