// ============================================================
// WooCommerce Integration
// Verifica si un email tiene pedidos completados en cdkeysperu.com
// y auto-crea el usuario con créditos basados en sus compras
// ============================================================

const WC_URL = process.env.WC_URL;
const WC_KEY = process.env.WC_CONSUMER_KEY;
const WC_SECRET = process.env.WC_CONSUMER_SECRET;

function isConfigured() {
    return WC_URL && WC_KEY && WC_SECRET && WC_KEY !== 'ck_TU_CONSUMER_KEY_AQUI';
}

// Buscar pedidos completados por email
async function getOrdersByEmail(email) {
    if (!isConfigured()) return null;

    const url = `${WC_URL}/wp-json/wc/v3/orders?search=${encodeURIComponent(email)}&status=completed&per_page=100`;

    try {
        const response = await fetch(url, {
            headers: {
                'Authorization': 'Basic ' + Buffer.from(`${WC_KEY}:${WC_SECRET}`).toString('base64')
            },
            signal: AbortSignal.timeout(10000)
        });

        if (!response.ok) {
            console.error(`[WC] Error ${response.status}: ${await response.text()}`);
            return null;
        }

        const orders = await response.json();
        return orders;
    } catch (err) {
        console.error('[WC] Error consultando WooCommerce:', err.message);
        return null;
    }
}

// Contar cuántos CIDs le corresponden al cliente según sus compras
// Cada pedido completado = 1 CID (puedes ajustar esta lógica)
async function calculateCreditsForEmail(email) {
    const orders = await getOrdersByEmail(email);
    if (!orders) return { found: false, credits: 0, orders: [] };

    // Filtrar solo pedidos que coincidan exactamente con el email
    const matching = orders.filter(o =>
        o.billing?.email?.toLowerCase() === email.toLowerCase()
    );

    if (matching.length === 0) return { found: false, credits: 0, orders: [] };

    // Cada pedido = 1 crédito CID
    // Puedes personalizar: por ejemplo, si un producto tiene "CID x5" dar 5 créditos
    let totalCredits = 0;
    const orderSummaries = [];

    for (const order of matching) {
        let orderCredits = 0;
        for (const item of order.line_items || []) {
            // Si el producto tiene "CID" en el nombre, contar la cantidad
            const name = (item.name || '').toLowerCase();
            if (name.includes('cid') || name.includes('confirmation') || name.includes('activat')) {
                orderCredits += item.quantity || 1;
            } else {
                // Producto normal = 1 CID por defecto
                orderCredits += 1;
            }
        }
        totalCredits += orderCredits;
        orderSummaries.push({
            id: order.id,
            date: order.date_created,
            total: order.total,
            items: order.line_items?.map(i => i.name).join(', '),
            credits: orderCredits
        });
    }

    return { found: true, credits: totalCredits, orders: orderSummaries };
}

module.exports = { isConfigured, getOrdersByEmail, calculateCreditsForEmail };
