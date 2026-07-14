function defineMethod(target, name, implementation) {
  if (typeof target[name] === "function") return;
  Object.defineProperty(target, name, {
    configurable: true,
    writable: true,
    value: implementation,
  });
}

defineMethod(String.prototype, "replaceAll", function replaceAll(search, replacement) {
  if (search instanceof RegExp) {
    if (!search.global) throw new TypeError("replaceAll requires a global regular expression");
    return String(this).replace(search, replacement);
  }
  return String(this).split(String(search)).join(String(replacement));
});

defineMethod(Array.prototype, "at", function at(index) {
  const length = this.length >>> 0;
  const normalized = Math.trunc(Number(index) || 0);
  const position = normalized < 0 ? length + normalized : normalized;
  return position < 0 || position >= length ? undefined : this[position];
});

defineMethod(Array.prototype, "flatMap", function flatMap(callback, thisArg) {
  return this.reduce((items, item, index, source) => {
    const mapped = callback.call(thisArg, item, index, source);
    return items.concat(mapped);
  }, []);
});
