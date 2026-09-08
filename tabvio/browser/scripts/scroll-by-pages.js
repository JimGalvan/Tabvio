(amount => {
    const ascend = (node) => {
        if (node.parentElement) return node.parentElement;
        const root = node.getRootNode();
        return root instanceof ShadowRoot ? root.host : null;
    };

    const deepestElementAt = (x, y) => {
        let node = document.elementFromPoint(x, y);
        while (node && node.shadowRoot) {
            const deeper = node.shadowRoot.elementFromPoint(x, y);
            if (!deeper || deeper === node) break;
            node = deeper;
        }
        return node;
    };

    const scrollingContainer = () => {
        const middle = deepestElementAt(innerWidth / 2, innerHeight / 2);
        for (let node = middle; node; node = ascend(node)) {
            const overflow = getComputedStyle(node).overflowY;
            const scrolls = overflow === 'auto' || overflow === 'scroll';
            if (scrolls && node.scrollHeight > node.clientHeight + 1) return node;
        }
        return document.scrollingElement || document.documentElement;
    };

    const container = scrollingContainer();
    const viewport = container.clientHeight || innerHeight;
    container.scrollBy(0, viewport * amount);

    return {
        current: container.scrollTop,
        maximum: Math.max(container.scrollHeight - viewport, 0)
    };
})
