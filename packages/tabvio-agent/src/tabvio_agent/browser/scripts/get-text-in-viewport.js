(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const text = [];
    let node;

    while ((node = walker.nextNode())) {
        const value = node.textContent.replace(/\s+/g, ' ').trim();
        const parent = node.parentElement;
        if (!value || !parent) continue;

        const style = getComputedStyle(parent);
        if (style.display === 'none' || style.visibility === 'hidden') continue;

        const range = document.createRange();
        range.selectNodeContents(node);
        const visible = [...range.getClientRects()].some(rect =>
            rect.width > 0 && rect.height > 0 &&
            rect.bottom > 0 && rect.top < innerHeight &&
            rect.right > 0 && rect.left < innerWidth
        );

        if (visible) text.push(value);
    }

    return text.join('\n');
})()
