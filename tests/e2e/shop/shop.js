// Shared state for the shop pages.
//
// sessionStorage rather than a query string, so "add to cart" is a real state
// change the agent has to perform: an agent that jumps straight to cart.html
// finds it empty, the same way item.html says "no item selected".

var Shop = (function () {
  var CART_KEY = 'tabvio-shop-cart';
  var SHIPPING_KEY = 'tabvio-shop-shipping';

  var SHIPPING_FEE = 6.95;
  var TAX_RATE = 0.0825;

  function read(key) {
    try {
      return JSON.parse(sessionStorage.getItem(key) || 'null');
    } catch (error) {
      return null;
    }
  }

  function write(key, value) {
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      /* A blocked store just means the next page shows an empty cart. */
    }
  }

  function money(amount) {
    return '$' + amount.toFixed(2);
  }

  function totals(cart) {
    if (!cart) {
      return {subtotal: 0, shipping: 0, tax: 0, total: 0};
    }
    var subtotal = cart.unitPrice * cart.quantity;
    var tax = Math.round(subtotal * TAX_RATE * 100) / 100;
    return {
      subtotal: subtotal,
      shipping: SHIPPING_FEE,
      tax: tax,
      total: subtotal + SHIPPING_FEE + tax
    };
  }

  function paintCartCount() {
    var badge = document.getElementById('cart-count');
    if (!badge) return;
    var cart = read(CART_KEY);
    badge.textContent = cart ? String(cart.quantity) : '0';
  }

  // Fills any element carrying data-total="subtotal|shipping|tax|total".
  function paintTotals(cart) {
    var amounts = totals(cart);
    var cells = document.querySelectorAll('[data-total]');
    for (var index = 0; index < cells.length; index++) {
      var cell = cells[index];
      cell.textContent = money(amounts[cell.getAttribute('data-total')]);
    }
    return amounts;
  }

  return {
    readCart: function () { return read(CART_KEY); },
    saveCart: function (cart) { write(CART_KEY, cart); },
    clearCart: function () { write(CART_KEY, null); },
    readShipping: function () { return read(SHIPPING_KEY); },
    saveShipping: function (shipping) { write(SHIPPING_KEY, shipping); },
    money: money,
    totals: totals,
    paintCartCount: paintCartCount,
    paintTotals: paintTotals
  };
})();
