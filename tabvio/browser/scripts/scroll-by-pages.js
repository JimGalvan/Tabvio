(amount => {
    window.scrollBy(0, innerHeight * amount);

    return {
        current: scrollY,
        maximum: Math.max(document.documentElement.scrollHeight - innerHeight, 0)
    };
})
