(async () => {
    const MAX_ELEMENTS = 300;
    const MAX_LABEL_CHARS = 120;
    const MAX_VALUE_CHARS = 40;
    const MAX_SIGNATURE_TEXT_CHARS = 40;
    const MAX_HREF_CHARS = 60;
    const MAX_PAGE_TEXT_CHARS = 3000;
    const MIN_CLICKABLE_PX = 3;
    const CLIP_TIMEOUT_MS = 250;

    const INTERACTIVE_TAGS = ['a','button','input','select','textarea','summary','label'];
    const INTERACTIVE_ROLES = ['button','link','checkbox','radio','tab','menuitem',
        'option','switch','combobox','searchbox','textbox','menuitemcheckbox',
        'menuitemradio','slider','spinbutton','treeitem','gridcell','listbox'];

    const TOGGLE_INPUT_TYPES = ['checkbox','radio'];
    const BUTTON_INPUT_TYPES = ['submit','button','reset'];
    const VALUELESS_INPUT_TYPES = ['checkbox','radio','submit','button','reset',
        'image','file','hidden','range','color'];
    const PRIVATE_INPUT_TYPES = ['password','email','tel'];
    const PRIVATE_FIELD_WORDS =
        /(^|[^a-z])(card|cvv|cvc|csc|ssn|sin|iban|swift|routing|account|passcode|pin|secret|token)([^a-z]|$)/i;

    const CLICK_POINT_FRACTIONS = [
        [0.5, 0.5],
        [0.5, 0.25], [0.5, 0.75], [0.25, 0.5], [0.75, 0.5],
        [0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]
    ];

    const EXPLICIT = 'explicit';
    const INFERRED = 'inferred';

    const collapseWhitespace = (value) => (value || '').replace(/\s+/g, ' ').trim();

    const ascend = (node) => {
        if (node.parentElement) return node.parentElement;
        const root = node.getRootNode();
        return root instanceof ShadowRoot ? root.host : null;
    };

    const elementsInTree = (root) => {
        const found = [];
        for (const element of root.querySelectorAll('*')) {
            found.push(element);
            if (element.shadowRoot) found.push(...elementsInTree(element.shadowRoot));
        }
        return found;
    };

    const hasOwnPointerCursor = (element) => {
        if (getComputedStyle(element).cursor !== 'pointer') return false;
        const parent = ascend(element);
        return !parent || getComputedStyle(parent).cursor !== 'pointer';
    };

    const interactiveKind = (element) => {
        const tagName = element.tagName.toLowerCase();
        const role = element.getAttribute('role');

        if (INTERACTIVE_TAGS.includes(tagName)) return EXPLICIT;
        if (role && INTERACTIVE_ROLES.includes(role)) return EXPLICIT;
        if (element.hasAttribute('onclick') || element.isContentEditable) return EXPLICIT;
        if (element.tabIndex >= 0 && tagName !== 'body') return INFERRED;
        if (hasOwnPointerCursor(element)) return INFERRED;
        return null;
    };

    const isBigEnoughToClick = (rect) =>
        rect.width >= MIN_CLICKABLE_PX && rect.height >= MIN_CLICKABLE_PX;

    const hasVisibleOwnStyle = (element) => {
        const style = getComputedStyle(element);
        return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
    };

    const isInHiddenSubtree = (element) => {
        for (let node = element; node; node = ascend(node)) {
            if (getComputedStyle(node).opacity === '0') return true;
            if (node.getAttribute('aria-hidden') === 'true') return true;
            if (node.hasAttribute('inert')) return true;
        }
        return false;
    };

    const observedClips = (elements) => new Promise((resolve) => {
        const clips = new Map();
        const finish = () => {
            clearTimeout(timer);
            observer.disconnect();
            resolve(clips);
        };
        const observer = new IntersectionObserver((entries) => {
            for (const entry of entries) clips.set(entry.target, entry.intersectionRect);
            if (clips.size >= elements.length) finish();
        });
        const timer = setTimeout(finish, CLIP_TIMEOUT_MS);
        for (const element of elements) observer.observe(element);
    });

    const deepestElementAt = (x, y) => {
        let node = document.elementFromPoint(x, y);
        while (node && node.shadowRoot) {
            const deeper = node.shadowRoot.elementFromPoint(x, y);
            if (!deeper || deeper === node) break;
            node = deeper;
        }
        return node;
    };

    const hitTestReaches = (element, x, y) => {
        const hit = deepestElementAt(x, y);
        if (!hit) return false;
        for (let node = hit; node; node = ascend(node)) {
            if (node === element) return true;
        }
        return false;
    };

    const clickPointWithin = (element, clip) => {
        for (const [across, down] of CLICK_POINT_FRACTIONS) {
            const x = clip.left + clip.width * across;
            const y = clip.top + clip.height * down;
            if (hitTestReaches(element, x, y)) return {x: x, y: y};
        }
        return null;
    };

    const isPrivateField = (element) => {
        if (element.dataset.tabvioSensitive) return true;
        if (PRIVATE_INPUT_TYPES.includes(element.type)) return true;

        const autocomplete = (element.getAttribute('autocomplete') || '').toLowerCase();
        if (autocomplete.startsWith('cc-')) return true;

        const identity = [
            element.getAttribute('name'),
            element.id,
            element.getAttribute('aria-label')
        ].join(' ');
        return PRIVATE_FIELD_WORDS.test(identity);
    };

    const holdsTypedValue = (element) =>
        element.tagName === 'TEXTAREA'
        || (element.tagName === 'INPUT' && !VALUELESS_INPUT_TYPES.includes(element.type));

    const textFromLabelledBy = (element) => {
        const ids = (element.getAttribute('aria-labelledby') || '').split(/\s+/);
        const root = element.getRootNode();
        return ids
            .map((id) => id && root.getElementById && root.getElementById(id))
            .filter(Boolean)
            .map((target) => target.innerText)
            .join(' ');
    };

    const textFromBoundLabel = (element) => {
        const labels = element.labels;
        return labels && labels.length ? labels[0].innerText : '';
    };

    const textFromImageAlt = (element) => {
        const image = element.querySelector('img[alt], area[alt], input[type=image][alt]');
        return image ? image.getAttribute('alt') : '';
    };

    const textFromButtonValue = (element) =>
        element.tagName === 'INPUT' && BUTTON_INPUT_TYPES.includes(element.type)
            ? element.value
            : '';

    const textFromContent = (element) =>
        element.tagName === 'SELECT' ? '' : element.innerText;

    const accessibleName = (element) => {
        const candidates = [
            textFromLabelledBy(element),
            element.getAttribute('aria-label'),
            textFromBoundLabel(element),
            element.getAttribute('placeholder'),
            element.getAttribute('title'),
            textFromImageAlt(element),
            textFromButtonValue(element),
            textFromContent(element)
        ];

        for (const candidate of candidates) {
            const text = collapseWhitespace(candidate);
            if (text) return text.slice(0, MAX_LABEL_CHARS);
        }
        return '';
    };

    const approximateSignature = (element, tagName, text) => [
        tagName,
        element.id || '',
        element.getAttribute('name') || '',
        element.getAttribute('href') || '',
        text.slice(0, MAX_SIGNATURE_TEXT_CHARS)
    ].join('|');

    const linkTarget = (href) => {
        let url;
        try {
            url = new URL(href, location.href);
        } catch (error) {
            return href.split('?')[0];
        }
        const path = url.origin === location.origin ? url.pathname : url.origin + url.pathname;
        return url.search ? path + '?...' : path;
    };

    const describeSelection = (element) => {
        if (element.tagName !== 'SELECT') return '';
        const option = element.selectedOptions && element.selectedOptions[0];
        const label = option ? collapseWhitespace(option.textContent) : '';
        return label ? 'selected=' + label.slice(0, MAX_VALUE_CHARS) : '';
    };

    const describeChecked = (element) => {
        const isToggle = element.tagName === 'INPUT'
            && TOGGLE_INPUT_TYPES.includes(element.type);
        if (isToggle) return element.checked ? 'checked' : 'unchecked';

        const checked = element.getAttribute('aria-checked');
        if (checked === 'true') return 'checked';
        if (checked === 'false') return 'unchecked';
        return '';
    };

    const describeExpanded = (element) => {
        const expanded = element.getAttribute('aria-expanded');
        if (expanded === 'true') return 'expanded';
        if (expanded === 'false') return 'collapsed';
        return '';
    };

    const describeValue = (element) => {
        if (!holdsTypedValue(element)) return '';
        const value = collapseWhitespace(element.value);
        if (!value) return 'empty';
        if (isPrivateField(element)) return 'filled';
        return 'value=' + value.slice(0, MAX_VALUE_CHARS);
    };

    const isDisabled = (element) =>
        element.disabled === true || element.getAttribute('aria-disabled') === 'true';

    const describeAttributes = (element) => {
        const attributes = [];
        const role = element.getAttribute('role');
        const href = element.getAttribute('href');

        if (role) attributes.push('role=' + role);
        if (element.type) attributes.push('type=' + element.type);
        if (href) attributes.push('href=' + linkTarget(href).slice(0, MAX_HREF_CHARS));

        for (const state of [
            describeSelection(element),
            describeChecked(element),
            describeExpanded(element),
            describeValue(element)
        ]) {
            if (state) attributes.push(state);
        }

        if (element.required) attributes.push('required');
        if (isDisabled(element)) attributes.push('disabled');

        return attributes.join(' ');
    };

    const describe = (entry) => {
        const tagName = entry.element.tagName.toLowerCase();
        const text = accessibleName(entry.element);

        return {
            signature: approximateSignature(entry.element, tagName, text),
            tag: tagName,
            text: text,
            attrs: describeAttributes(entry.element),
            cx: entry.point.x,
            cy: entry.point.y
        };
    };

    const keptElements = new WeakSet();

    const hasKeptAncestor = (element) => {
        for (let parent = ascend(element); parent; parent = ascend(parent)) {
            if (keptElements.has(parent)) return true;
        }
        return false;
    };

    const candidates = [];
    for (const element of elementsInTree(document)) {
        const kind = interactiveKind(element);
        if (!kind) continue;
        if (!hasVisibleOwnStyle(element)) continue;
        if (!isBigEnoughToClick(element.getBoundingClientRect())) continue;
        if (isInHiddenSubtree(element)) continue;
        candidates.push({element: element, kind: kind});
    }

    const clips = candidates.length ? await observedClips(
        candidates.map((candidate) => candidate.element)
    ) : new Map();

    const reachable = [];
    for (const candidate of candidates) {
        if (candidate.kind === INFERRED && hasKeptAncestor(candidate.element)) continue;

        const clip = clips.get(candidate.element);
        if (!clip || !isBigEnoughToClick(clip)) continue;

        const point = clickPointWithin(candidate.element, clip);
        if (!point) continue;

        const rect = candidate.element.getBoundingClientRect();
        keptElements.add(candidate.element);
        reachable.push({
            element: candidate.element,
            point: point,
            order: reachable.length,
            isLink: candidate.element.tagName === 'A',
            wholeElementVisible: clip.width >= rect.width - 1 && clip.height >= rect.height - 1
        });
    }

    const namesAControlAlready = (entry) =>
        entry.element.tagName === 'LABEL'
        && entry.element.control
        && keptElements.has(entry.element.control);

    const rankOf = (entry) => (entry.isLink ? 2 : 0) + (entry.wholeElementVisible ? 0 : 1);

    const found = reachable
        .filter((entry) => !namesAControlAlready(entry))
        .sort((first, second) => rankOf(first) - rankOf(second) || first.order - second.order)
        .slice(0, MAX_ELEMENTS)
        .map(describe);

    const scrollingContainer = () => {
        const middle = deepestElementAt(innerWidth / 2, innerHeight / 2);
        for (let node = middle; node; node = ascend(node)) {
            const overflow = getComputedStyle(node).overflowY;
            const scrolls = overflow === 'auto' || overflow === 'scroll';
            if (scrolls && node.scrollHeight > node.clientHeight + 1) return node;
        }
        return document.scrollingElement || document.documentElement;
    };

    const pagesBelowIn = (container) => {
        const viewport = container.clientHeight || innerHeight;
        const remaining = container.scrollHeight - viewport - container.scrollTop;
        return Math.max(0, remaining / Math.max(viewport, 1));
    };

    const renderedTextOf = (element) => collapseWhitespace(element.innerText);

    return JSON.stringify({
        url: location.href,
        title: document.title,
        elements: found,
        pageText: renderedTextOf(document.body).slice(0, MAX_PAGE_TEXT_CHARS),
        pagesBelow: pagesBelowIn(scrollingContainer())
    });
})()
