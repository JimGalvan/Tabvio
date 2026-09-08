(() => {
    const CARD_AUTOCOMPLETE_VALUES = new Set([
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

    const PAYMENT_PROVIDER_HOST_PATTERNS = [
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

    const signals = [];
    const seen = new Set();

    function getPaymentProviderFromSource(iframeSourceUrl) {
        let hostname;
        try {
            hostname = new URL(iframeSourceUrl, window.location.href).hostname;
        } catch (error) {
            return null;
        }

        for (const [providerName, hostnamePattern] of PAYMENT_PROVIDER_HOST_PATTERNS) {
            const hostnameMatches = hostnamePattern.test(hostname);
            if (hostnameMatches) {
                return providerName;
            }
        }
        return null;
    }

    function record(type, value) {
        const key = type + '|' + value;
        if (seen.has(key)) return;
        seen.add(key);
        signals.push({type: type, value: value});
    }

    const fieldsWithAutocomplete = document.querySelectorAll(
        'input[autocomplete], select[autocomplete]',
    );

    for (const field of fieldsWithAutocomplete) {
        const autocompleteValues = (field.getAttribute('autocomplete') || '')
            .toLowerCase()
            .split(/\s+/);

        for (const autocompleteValue of autocompleteValues) {
            if (CARD_AUTOCOMPLETE_VALUES.has(autocompleteValue)) {
                record('card-autocomplete', autocompleteValue);
            }
        }
    }

    const iframes = document.querySelectorAll('iframe');
    for (const iframe of iframes) {
        const iframeSourceUrl = iframe.getAttribute('src');
        const paymentProvider = getPaymentProviderFromSource(iframeSourceUrl);
        if (paymentProvider) {
            record('hosted-payment-field', paymentProvider);
            continue;
        }

        const name = iframe.getAttribute('name') || '';
        for (const [label, pattern] of HOSTED_FRAME_NAMES) {
            if (pattern.test(name)) record('hosted-payment-field', label);
        }
    }

    const scripts = document.querySelectorAll('script[src]');
    for (const script of scripts) {
        const scriptSourceUrl = script.getAttribute('src');
        const processor = getPaymentProviderFromSource(scriptSourceUrl);
        if (processor) record('payment-sdk', processor);
    }

    return JSON.stringify({url: location.href, signals: signals});
})()
