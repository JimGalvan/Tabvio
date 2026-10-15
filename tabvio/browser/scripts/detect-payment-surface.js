(() => {
    const CARD_ENTRY_AUTOCOMPLETE_VALUES = new Set([
        'cc-number',
        'cc-exp',
        'cc-exp-month',
        'cc-exp-year',
        'cc-csc',
    ]);

    const PAY_BUTTON_PATTERNS = [
        ['place-order', /\bplace\s+(your\s+|the\s+)?order\b/],
        ['complete-purchase', /\bcomplete\s+(your\s+|the\s+)?(order|purchase|payment)\b/],
        ['confirm-payment', /\bconfirm\s+(and\s+)?(pay|payment|purchase|order)\b/],
        ['submit-payment', /\bsubmit\s+(your\s+|the\s+)?(order|payment)\b/],
        ['authorize-payment', /\bauthori[sz]e\s+(the\s+)?payment\b/],
        ['pay-now', /^pay(\s+(now|securely))?$/],
        ['pay-amount', /^pay\s+[$€£¥]/],
    ];

    const BUTTON_SELECTOR =
        'button, input[type="submit"], input[type="button"], [role="button"]';
    const LONGEST_BUTTON_LABEL = 80;
    const STRIPE_ORIGIN_PATTERN = /^https:\/\/js\.stripe\.com(?:\/|$)/;
    const STRIPE_FRAME_NAME_PATTERN = /^__privateStripeFrame\d+$/;
    const CARD_FIELD_LABEL_PATTERN = /\b(card|cardnumber|expiry|expiration|cvc|cvv|security[\s-]+code)\b/i;
    const BRAINTREE_FIELD_NAME_PATTERN = /^braintree-hosted-field-(number|expirationDate|expirationMonth|expirationYear|cvv)$/;

    const signals = [];
    const seen = new Set();

    function record(type, value) {
        const key = type + '|' + value;
        if (seen.has(key)) return;
        seen.add(key);
        signals.push({type: type, value: value});
    }

    function readLabel(element) {
        const label =
            element.getAttribute('aria-label') ||
            element.value ||
            element.textContent ||
            '';
        return label.toLowerCase().replace(/\s+/g, ' ').trim();
    }

    const fieldsWithAutocomplete = document.querySelectorAll(
        'input[autocomplete], select[autocomplete]',
    );

    for (const field of fieldsWithAutocomplete) {
        if (field.disabled || field.type === 'hidden') {
            continue;
        }

        const autocompleteValues = (field.getAttribute('autocomplete') || '')
            .toLowerCase()
            .split(/\s+/);

        for (const autocompleteValue of autocompleteValues) {
            if (CARD_ENTRY_AUTOCOMPLETE_VALUES.has(autocompleteValue)) {
                record('card-autocomplete', autocompleteValue);
            }
        }
    }

    for (const iframe of document.querySelectorAll('iframe')) {
        const name = iframe.getAttribute('name') || '';
        const title = iframe.getAttribute('title') || '';
        const isStripeFrame = STRIPE_ORIGIN_PATTERN.test(iframe.src) ||
            STRIPE_FRAME_NAME_PATTERN.test(name);

        if (isStripeFrame && CARD_FIELD_LABEL_PATTERN.test(name + ' ' + title)) {
            record('hosted-field', 'stripe');
        }
        if (BRAINTREE_FIELD_NAME_PATTERN.test(name)) {
            record('hosted-field', 'braintree');
        }
    }

    for (const button of document.querySelectorAll(BUTTON_SELECTOR)) {
        const label = readLabel(button);
        if (!label || label.length > LONGEST_BUTTON_LABEL) {
            continue;
        }

        for (const [patternName, pattern] of PAY_BUTTON_PATTERNS) {
            if (pattern.test(label)) {
                record('pay-button', patternName);
                break;
            }
        }
    }

    return JSON.stringify({url: location.href, signals: signals});
})()
