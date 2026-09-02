(() => {
    const MAX_ELEMENTS = 500;
    const INTERACTIVE_TAGS = ['a','button','input','select','textarea','summary','label'];
    const INTERACTIVE_ROLES = ['button','link','checkbox','radio','tab','menuitem',
        'option','switch','combobox','searchbox','textbox'];

    // Two confidence levels. 'strong' means the element declares itself
    // actionable, so it survives even inside another actionable element (a
    // Save button within a clickable card). 'weak' is inferred from styling or
    // focusability and is dropped when an ancestor was already kept.
    const interactiveKind = (element) => {
        const tagName = element.tagName.toLowerCase();
        if (INTERACTIVE_TAGS.includes(tagName)) return 'strong';
        const role = element.getAttribute('role');
        if (role && INTERACTIVE_ROLES.includes(role)) return 'strong';
        if (element.hasAttribute('onclick') || element.isContentEditable) return 'strong';
        if (element.tabIndex >= 0 && tagName !== 'body') return 'weak';

        // cursor is an inherited property, so every div inside a clickable cell
        // reports 'pointer' too. Only credit the element that introduces it.
        if (getComputedStyle(element).cursor !== 'pointer') return null;
        const parent = element.parentElement;
        if (parent && getComputedStyle(parent).cursor === 'pointer') return null;
        return 'weak';
    };

    const isVisible = (element, rect) => {
        if (rect.width < 3 || rect.height < 3) return false;
        if (rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) return false;
        const style = getComputedStyle(element);
        return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
    };

    // Collapsing descendants would otherwise lose their labels: on a Google
    // Flights date cell the visible text is "5 $307" while the full
    // "Saturday, September 5, 2026" sits on a child. Absorb both.
    const MAX_ROLLUP_LABELS = 3;

    const label = (element) => {
        const parts = [];
        const push = (value) => {
            const text = (value || '').replace(/\s+/g, ' ').trim();
            if (text && !parts.some(part => part.includes(text))) parts.push(text);
        };

        push(element.getAttribute('aria-label'));
        push(element.getAttribute('placeholder'));
        if (element.tagName === 'INPUT' && element.type !== 'password' && !element.dataset.tabvioSensitive) push(element.value);

        let budget = MAX_ROLLUP_LABELS;
        for (const descendant of element.querySelectorAll('[aria-label]')) {
            if (budget-- <= 0) break;
            push(descendant.getAttribute('aria-label'));
        }

        push(element.innerText);
        return parts.join(' ').slice(0, 120);
    };

    const kept = new WeakSet();

    const hasKeptAncestor = (element) => {
        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
            if (kept.has(parent)) return true;
        }
        return false;
    };

    const found = [];
    for (const element of document.querySelectorAll('*')) {
        if (found.length >= MAX_ELEMENTS) break;

        const kind = interactiveKind(element);
        if (!kind) continue;
        if (kind === 'weak' && hasKeptAncestor(element)) continue;

        const rect = element.getBoundingClientRect();
        if (!isVisible(element, rect)) continue;

        const centerX = rect.left + rect.width / 2, centerY = rect.top + rect.height / 2;

        // Poor-man's paint-order filtering: if something else is on top at the
        // element's centre, it is occluded (cookie banner, modal) - skip it.
        const topmost = document.elementFromPoint(centerX, centerY);
        if (topmost && topmost !== element && !element.contains(topmost)) continue;

        const tagName = element.tagName.toLowerCase();
        const text = label(element);

        // Identity across observations. browser-use uses CDP backendNodeId,
        // which is genuinely stable; this signature is only approximate.
        const signature = [tagName, element.id || '', element.getAttribute('name') || '',
            element.getAttribute('href') || '', text.slice(0, 40)].join('|');

        const attributes = [];
        if (element.type) attributes.push('type=' + element.type);
        if (element.getAttribute('href')) attributes.push('href=' + element.getAttribute('href').slice(0, 60));
        if (element.disabled) attributes.push('disabled');

        kept.add(element);
        found.push({ signature, tag: tagName, text, attrs: attributes.join(' '), cx: centerX, cy: centerY });
    }

    // Readable content, capped. innerText (not textContent) already respects
    // CSS visibility, so hidden copy stays out for the same reason it does above.
    const pageText = (document.body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 3000);

    return JSON.stringify({
        url: location.href,
        title: document.title,
        elements: found,
        pageText: pageText,
        pagesBelow: (document.body.scrollHeight - innerHeight - scrollY) / Math.max(innerHeight, 1)
    });
})()
