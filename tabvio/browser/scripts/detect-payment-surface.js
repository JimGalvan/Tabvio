(() => {
    const CARD_TOKENS = new Set([
        'cc-name',
        'cc-given-name',
        'cc-additional-name',
        'cc-family-name',
        'cc-number',
        'cc-exp',
        'cc-exp-month',
        'cc-exp-year',
        'cc-csc',
        'cc-type',
    ]);

    const PROCESSOR_HOSTS = [
        ['Stripe', /(^|\.)stripe\.com$/],
        ['Braintree', /(^|\.)braintreegateway\.com$/],
        ['Adyen', /(^|\.)adyen\.com$/],
        ['PayPal', /(^|\.)paypal\.com$/],
        ['Square', /(^|\.)squarecdn\.com$/],
        ['Checkout.com', /(^|\.)checkout\.com$/],
        ['Klarna', /(^|\.)klarna(cdn)?\.(com|net)$/],
        ['Affirm', /(^|\.)affirm\.com$/],
        ['Amazon Pay', /(^|\.)payments-amazon\.com$/],
        ['Google Pay', /(^|\.)pay\.google\.com$/],
    ];

    const HOSTED_FRAME_NAMES = [
        ['Stripe', /^__privateStripeFrame/],
        ['Braintree', /^braintree-hosted-field/],
        ['Adyen', /^adyen-checkout/],
    ];

    const processorFor = (value) => {
        let hostname;
        try {
            hostname = new URL(value, location.href).hostname.toLowerCase();
        } catch (error) {
            return null;
        }

        for (const [name, pattern] of PROCESSOR_HOSTS) {
            if (pattern.test(hostname)) return name;
        }
        return null;
    };

    const signals = [];
    const seen = new Set();
    const record = (kind, detail) => {
        const key = kind + '|' + detail;
        if (seen.has(key)) return;
        seen.add(key);
        signals.push({kind: kind, detail: detail});
    };

    const autofillable = document.querySelectorAll(
        'input[autocomplete], select[autocomplete]',
    );
    for (const field of autofillable) {
        const tokens = (field.getAttribute('autocomplete') || '')
            .toLowerCase()
            .split(/\s+/);
        for (const token of tokens) {
            if (CARD_TOKENS.has(token)) record('card-autocomplete', token);
        }
    }

    for (const frame of document.querySelectorAll('iframe')) {
        const processor = processorFor(frame.getAttribute('src'));
        if (processor) {
            record('hosted-payment-field', processor);
            continue;
        }

        const name = frame.getAttribute('name') || '';
        for (const [label, pattern] of HOSTED_FRAME_NAMES) {
            if (pattern.test(name)) record('hosted-payment-field', label);
        }
    }

    for (const script of document.querySelectorAll('script[src]')) {
        const processor = processorFor(script.getAttribute('src'));
        if (processor) record('payment-sdk', processor);
    }

    return JSON.stringify({url: location.href, signals: signals});
})()
