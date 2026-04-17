const { Telegraf } = require('telegraf');
const fs = require('fs');
const path = require('path');
const { getConfirmationID } = require('./cid_helper');
const { ocrAndGetCID } = require('./ocr');
const db = require('./db');

const BOT_TOKEN = process.env.BOT_TOKEN;
const ADMIN_IDS = (process.env.ADMIN_IDS || '').split(',').map(s => s.trim()).filter(Boolean);

function startBot() {
    if (!BOT_TOKEN) {
        console.error('❌ BOT_TOKEN está vacío. Bot no iniciará.');
        return;
    }

    console.log(`🤖 Iniciando bot con token: ${BOT_TOKEN.substring(0, 10)}...`);
    console.log(`🔑 Admin IDs: ${ADMIN_IDS.join(', ') || 'NINGUNO'}`);

    const bot = new Telegraf(BOT_TOKEN);

    function formatIID(iid) { return iid.match(/.{1,7}/g)?.join('-') || iid; }
    function formatCID(cid) {
        if (Array.isArray(cid)) return cid.join('-');
        if (typeof cid === 'string') return cid.match(/.{1,6}/g)?.join('-') || cid;
        return JSON.stringify(cid);
    }

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

    // FOTOS
    bot.on('photo', async (ctx) => {
        const tgId = String(ctx.from.id);
        let user = db.findUserByTelegram(tgId);
        if (!user) user = db.createUser({ telegram_id: tgId, telegram_username: ctx.from.username });
        if (user.balance <= 0) return ctx.reply('❌ Sin créditos. Contacta al admin.');

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
                await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null,
                    `🔑 <b>@CdKeysPeru</b>\n\n` +
                    `<b>IID:</b>\n<code>${formatIID(result.iid)}</code>\n\n` +
                    `<b>CID:</b>\n<code>${cidStr}</code>\n\n` +
                    `<i>💰 -1 CID | Balance: ${bal} | ⏱ 00:${secs}</i>`,
                    { parse_mode: 'HTML', reply_markup: { inline_keyboard: [[{ text: '📋 Copiar CID', callback_data: `copy_${cidStr.replace(/-/g, '')}` }]] } }
                );
            } else {
                db.logTransaction(user.id, 'telegram', null, null, 'ocr_failed', elapsed, null);
                await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, '❌ No se pudo leer el IID. Intenta con foto más nítida o envía el IID como texto.');
            }
        } catch (err) {
            console.error('[BOT PHOTO ERROR]', err.message);
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null, '❌ Error. Intenta de nuevo.').catch(() => {});
        }
    });

    // TEXTO (IID directo)
    bot.on('text', async (ctx) => {
        if (ctx.message.text.startsWith('/')) return;
        const digits = ctx.message.text.replace(/\D/g, '');
        if (digits.length < 54) return;

        const tgId = String(ctx.from.id);
        let user = db.findUserByTelegram(tgId);
        if (!user) user = db.createUser({ telegram_id: tgId, telegram_username: ctx.from.username });
        if (user.balance <= 0) return ctx.reply('❌ Sin créditos.');

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
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null,
                `🔑 <b>@CdKeysPeru</b>\n\n` +
                `<b>IID:</b>\n<code>${formatIID(digits)}</code>\n\n` +
                `<b>CID:</b>\n<code>${cidStr}</code>\n\n` +
                `<i>💰 -1 CID | Balance: ${bal} | ⏱ 00:${secs}</i>`,
                { parse_mode: 'HTML', reply_markup: { inline_keyboard: [[{ text: '📋 Copiar CID', callback_data: `copy_${cidStr.replace(/-/g, '')}` }]] } }
            );
        } catch (err) {
            db.logTransaction(user.id, 'telegram', digits, null, 'error', Date.now() - startTime, null);
            await ctx.telegram.editMessageText(ctx.chat.id, msg.message_id, null,
                `❌ ${err.message === 'INVALID_CHECKSUM' ? 'Checksum inválido. Verifica el IID.' : err.message}`
            );
        }
    });

    // CALLBACK: Copiar CID
    bot.on('callback_query', async (ctx) => {
        const data = ctx.callbackQuery.data;
        if (data.startsWith('copy_')) {
            const cid = data.replace('copy_', '');
            await ctx.answerCbQuery(`CID copiado: ${cid}`, { show_alert: false });
        }
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
