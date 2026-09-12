(async () => {
    const MAX_ELEMENTS = 300;
    const MAX_LABEL_CHARS = 120;
    const MAX_VALUE_CHARS = 40;
    const MAX_SIGNATURE_TEXT_CHARS = 40;
    const MAX_HREF_CHARS = 60;
    const MAX_PAGE_TEXT_CHARS = 3000;
    const MIN_CLICKABLE_PX = 3;
    const CLIP_TIMEOUT_MS = 250;

    const INTERACTIVE_TAGS = ['a', 'button', 'input', 'select', 'textarea', 'summary', 'label'];
    const INTERACTIVE_ROLES = ['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem',
        'option', 'switch', 'combobox', 'searchbox', 'textbox', 'menuitemcheckbox',
        'menuitemradio', 'slider', 'spinbutton', 'treeitem', 'gridcell', 'listbox'];

    const TOGGLE_INPUT_TYPES = ['checkbox', 'radio'];
    const BUTTON_INPUT_TYPES = ['submit', 'button', 'reset'];
    const VALUELESS_INPUT_TYPES = ['checkbox', 'radio', 'submit', 'button', 'reset',
        'image', 'file', 'hidden', 'range', 'color'];
    const PRIVATE_INPUT_TYPES = ['password', 'email', 'tel'];
    const PRIVATE_FIELD_WORDS =
        /(^|[^a-z])(card|cvv|cvc|csc|ssn|sin|iban|swift|routing|account|passcode|pin|secret|token)([^a-z]|$)/i;

    const CLICK_POINT_FRACTIONS = [
        [0.5, 0.5],
        [0.5, 0.25], [0.5, 0.75], [0.25, 0.5], [0.75, 0.5],
        [0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]
    ];

    const EXPLICIT = 'explicit';
    const INFERRED = 'inferred';

    const keptElements = new WeakSet();

    function collapseWhitespace(value) {
        return (value || '').replace(/\s+/g, ' ').trim();
    }

    function ascendToParent(node) {
        if (node.parentElement) return node.parentElement;
        const root = node.getRootNode();
        return root instanceof ShadowRoot ? root.host : null;
    }

    function collectElementsInTree(root) {
        const found = [];
        for (const element of root.querySelectorAll('*')) {
            found.push(element);
            if (element.shadowRoot) found.push(...collectElementsInTree(element.shadowRoot));
        }
        return found;
    }

    function checkForOwnPointerCursor(element) {
        if (getComputedStyle(element).cursor !== 'pointer') return false;
        const parent = ascendToParent(element);
        return !parent || getComputedStyle(parent).cursor !== 'pointer';
    }

    function getInteractiveKind(element) {
        try {
            const tagName = element.tagName.toLowerCase();
            const role = element.getAttribute('role');

            if (INTERACTIVE_TAGS.includes(tagName)) return EXPLICIT;
            if (role && INTERACTIVE_ROLES.includes(role)) return EXPLICIT;
            if (element.hasAttribute('onclick') || element.isContentEditable) return EXPLICIT;
            if (element.tabIndex >= 0 && tagName !== 'body') return INFERRED;

            const hasOwnPointerCursor = checkForOwnPointerCursor(element);
            if (hasOwnPointerCursor) return INFERRED;
            return null;
        } catch (e) {
            return null
        }
    }

    function checkIfBigEnoughToClick(rect) {
        return rect.width >= MIN_CLICKABLE_PX && rect.height >= MIN_CLICKABLE_PX;
    }

    function checkForVisibleOwnStyle(element) {
        const style = getComputedStyle(element);
        return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
    }

    function checkIfInHiddenSubtree(element) {
        for (let node = element; node; node = ascendToParent(node)) {
            if (getComputedStyle(node).opacity === '0') return true;
            if (node.getAttribute('aria-hidden') === 'true') return true;
            if (node.hasAttribute('inert')) return true;
        }
        return false;
    }

    function observeClips(elements) {
        return new Promise((resolve) => {
            const clips = new Map();

            function finish() {
                clearTimeout(timer);
                observer.disconnect();
                resolve(clips);
            }

            const observer = new IntersectionObserver((entries) => {
                for (const entry of entries) clips.set(entry.target, entry.intersectionRect);
                if (clips.size >= elements.length) finish();
            });
            const timer = setTimeout(finish, CLIP_TIMEOUT_MS);
            for (const element of elements) observer.observe(element);
        });
    }

    function findDeepestElementAt(x, y) {
        let node = document.elementFromPoint(x, y);
        while (node && node.shadowRoot) {
            const deeper = node.shadowRoot.elementFromPoint(x, y);
            if (!deeper || deeper === node) break;
            node = deeper;
        }
        return node;
    }

    function checkIfHitTestReaches(element, x, y) {
        const hit = findDeepestElementAt(x, y);
        if (!hit) return false;
        for (let node = hit; node; node = ascendToParent(node)) {
            if (node === element) return true;
        }
        return false;
    }

    function findClickPointWithin(element, clip) {
        for (const [across, down] of CLICK_POINT_FRACTIONS) {
            const x = clip.left + clip.width * across;
            const y = clip.top + clip.height * down;

            const hitTestReaches = checkIfHitTestReaches(element, x, y);
            if (hitTestReaches) return {x: x, y: y};
        }
        return null;
    }

    function checkIfPrivateField(element) {
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
    }

    function checkIfHoldsTypedValue(element) {
        return element.tagName === 'TEXTAREA'
            || (element.tagName === 'INPUT' && !VALUELESS_INPUT_TYPES.includes(element.type));
    }

    function getTextFromLabelledBy(element) {
        const ids = (element.getAttribute('aria-labelledby') || '').split(/\s+/);
        const root = element.getRootNode();
        return ids
            .map((id) => id && root.getElementById && root.getElementById(id))
            .filter(Boolean)
            .map((target) => target.innerText)
            .join(' ');
    }

    function getTextFromBoundLabel(element) {
        const labels = element.labels;
        return labels && labels.length ? labels[0].innerText : '';
    }

    function getTextFromImageAlt(element) {
        const image = element.querySelector('img[alt], area[alt], input[type=image][alt]');
        return image ? image.getAttribute('alt') : '';
    }

    function getTextFromButtonValue(element) {
        return element.tagName === 'INPUT' && BUTTON_INPUT_TYPES.includes(element.type)
            ? element.value
            : '';
    }

    function getTextFromContent(element) {
        return element.tagName === 'SELECT' ? '' : element.innerText;
    }

    function getAccessibleName(element) {
        const candidates = [
            getTextFromLabelledBy(element),
            element.getAttribute('aria-label'),
            getTextFromBoundLabel(element),
            element.getAttribute('placeholder'),
            element.getAttribute('title'),
            getTextFromImageAlt(element),
            getTextFromButtonValue(element),
            getTextFromContent(element)
        ];

        for (const candidate of candidates) {
            const text = collapseWhitespace(candidate);
            if (text) return text.slice(0, MAX_LABEL_CHARS);
        }
        return '';
    }

    function buildApproximateSignature(element, tagName, text) {
        return [
            tagName,
            element.id || '',
            element.getAttribute('name') || '',
            element.getAttribute('href') || '',
            text.slice(0, MAX_SIGNATURE_TEXT_CHARS)
        ].join('|');
    }

    function getLinkTarget(href) {
        let url;
        try {
            url = new URL(href, location.href);
        } catch (error) {
            return href.split('?')[0];
        }
        const path = url.origin === location.origin ? url.pathname : url.origin + url.pathname;
        return url.search ? path + '?...' : path;
    }

    function describeSelection(element) {
        if (element.tagName !== 'SELECT') return '';
        const option = element.selectedOptions && element.selectedOptions[0];
        const label = option ? collapseWhitespace(option.textContent) : '';
        return label ? 'selected=' + label.slice(0, MAX_VALUE_CHARS) : '';
    }

    function describeChecked(element) {
        const isToggle = element.tagName === 'INPUT'
            && TOGGLE_INPUT_TYPES.includes(element.type);
        if (isToggle) return element.checked ? 'checked' : 'unchecked';

        const checked = element.getAttribute('aria-checked');
        if (checked === 'true') return 'checked';
        if (checked === 'false') return 'unchecked';
        return '';
    }

    function describeExpanded(element) {
        const expanded = element.getAttribute('aria-expanded');
        if (expanded === 'true') return 'expanded';
        if (expanded === 'false') return 'collapsed';
        return '';
    }

    function describeValue(element) {
        const holdsTypedValue = checkIfHoldsTypedValue(element);
        if (!holdsTypedValue) return '';

        const value = collapseWhitespace(element.value);
        if (!value) return 'empty';

        const isPrivateField = checkIfPrivateField(element);
        if (isPrivateField) return 'filled';
        return 'value=' + value.slice(0, MAX_VALUE_CHARS);
    }

    function checkIfDisabled(element) {
        return element.disabled === true || element.getAttribute('aria-disabled') === 'true';
    }

    function describeAttributes(element) {
        const attributes = [];
        const role = element.getAttribute('role');
        const href = element.getAttribute('href');

        if (role) attributes.push('role=' + role);
        if (element.type) attributes.push('type=' + element.type);
        if (href) attributes.push('href=' + getLinkTarget(href).slice(0, MAX_HREF_CHARS));

        for (const state of [
            describeSelection(element),
            describeChecked(element),
            describeExpanded(element),
            describeValue(element)
        ]) {
            if (state) attributes.push(state);
        }

        if (element.required) attributes.push('required');

        const isDisabled = checkIfDisabled(element);
        if (isDisabled) attributes.push('disabled');

        return attributes.join(' ');
    }

    function describeElement(entry) {
        const tagName = entry.element.tagName.toLowerCase();
        const text = getAccessibleName(entry.element);

        return {
            signature: buildApproximateSignature(entry.element, tagName, text),
            tag: tagName,
            text: text,
            attrs: describeAttributes(entry.element),
            cx: entry.point.x,
            cy: entry.point.y
        };
    }

    function checkForKeptAncestor(element) {
        for (let parent = ascendToParent(element); parent; parent = ascendToParent(parent)) {
            if (keptElements.has(parent)) return true;
        }
        return false;
    }

    function checkIfNamesAKeptControl(entry) {
        return entry.element.tagName === 'LABEL'
            && entry.element.control
            && keptElements.has(entry.element.control);
    }

    function calculateRank(entry) {
        return (entry.isLink ? 2 : 0) + (entry.wholeElementVisible ? 0 : 1);
    }

    function findScrollingContainer() {
        const middle = findDeepestElementAt(innerWidth / 2, innerHeight / 2);
        for (let node = middle; node; node = ascendToParent(node)) {
            const overflow = getComputedStyle(node).overflowY;
            const overflowScrolls = overflow === 'auto' || overflow === 'scroll';
            const hasHiddenContent = node.scrollHeight > node.clientHeight + 1;
            if (overflowScrolls && hasHiddenContent) return node;
        }
        return document.scrollingElement || document.documentElement;
    }

    function calculatePagesBelow(container) {
        const viewport = container.clientHeight || innerHeight;
        const remaining = container.scrollHeight - viewport - container.scrollTop;
        return Math.max(0, remaining / Math.max(viewport, 1));
    }

    function getRenderedText(element) {
        const documentHasNoBody = !element;
        if (documentHasNoBody) {
            return '';
        }
        return collapseWhitespace(element.innerText);
    }

    const candidates = [];
    for (const element of collectElementsInTree(document)) {
        const interactiveKind = getInteractiveKind(element);
        if (!interactiveKind) continue;

        const hasVisibleOwnStyle = checkForVisibleOwnStyle(element);
        if (!hasVisibleOwnStyle) continue;

        const isBigEnoughToClick = checkIfBigEnoughToClick(element.getBoundingClientRect());
        if (!isBigEnoughToClick) continue;

        const isInHiddenSubtree = checkIfInHiddenSubtree(element);
        if (isInHiddenSubtree) continue;

        candidates.push({element: element, kind: interactiveKind});
    }

    const clips = candidates.length ? await observeClips(
        candidates.map((candidate) => candidate.element)
    ) : new Map();

    const reachable = [];
    for (const candidate of candidates) {
        const isInferred = candidate.kind === INFERRED;
        if (isInferred && checkForKeptAncestor(candidate.element)) continue;

        const clip = clips.get(candidate.element);
        const clipIsBigEnough = clip && checkIfBigEnoughToClick(clip);
        if (!clipIsBigEnough) continue;

        const point = findClickPointWithin(candidate.element, clip);
        if (!point) continue;

        const rect = candidate.element.getBoundingClientRect();
        const isLink = candidate.element.tagName === 'A';
        const wholeElementVisible =
            clip.width >= rect.width - 1 && clip.height >= rect.height - 1;

        keptElements.add(candidate.element);
        reachable.push({
            element: candidate.element,
            point: point,
            order: reachable.length,
            isLink: isLink,
            wholeElementVisible: wholeElementVisible
        });
    }

    const found = reachable
        .filter((entry) => !checkIfNamesAKeptControl(entry))
        .sort((first, second) =>
            calculateRank(first) - calculateRank(second) || first.order - second.order)
        .slice(0, MAX_ELEMENTS)
        .map(describeElement);

    return JSON.stringify({
        url: location.href,
        title: document.title,
        elements: found,
        pageText: getRenderedText(document.body).slice(0, MAX_PAGE_TEXT_CHARS),
        pagesBelow: calculatePagesBelow(findScrollingContainer())
    });
})()
