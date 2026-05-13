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

// Buscar pedidos completados por email (con filtro exacto post-respuesta)
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
        
        // FILTRO EXACTO: Solo devolver pedidos cuyo billing email coincida exactamente
        const exactMatches = orders.filter(o => 
            o.billing?.email?.toLowerCase() === email.toLowerCase()
        );
        
        return exactMatches;
    } catch (err) {
        console.error('[WC] Error consultando WooCommerce:', err.message);
        return null;
    }
}

// Buscar un pedido por ID
async function getOrderById(orderId) {
    if (!isConfigured()) return null;

    const url = `${WC_URL}/wp-json/wc/v3/orders/${encodeURIComponent(orderId)}`;

    try {
        const response = await fetch(url, {
            headers: {
                'Authorization': 'Basic ' + Buffer.from(`${WC_KEY}:${WC_SECRET}`).toString('base64')
            },
            signal: AbortSignal.timeout(10000)
        });

        if (!response.ok) {
            if (response.status === 404) return null; // Pedido no existe
            console.error(`[WC] Error ${response.status}: ${await response.text()}`);
            return null;
        }

        const order = await response.json();
        return order;
    } catch (err) {
        console.error('[WC] Error consultando WooCommerce por ID:', err.message);
        return null;
    }
}

module.exports = { isConfigured, getOrdersByEmail, getOrderById };
