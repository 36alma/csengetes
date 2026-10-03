// Az app.js betoltese egy minimalis, mindent elfogado DOM-helyettesben.
// Hasznalat: node app_init_harness.js <app.js> <valaszok.json>
// Kimenet (stdout, JSON): a lekert utvonalak, a select allapota es a nem kezelt hibak.
"use strict";

const fs = require("fs");
const vm = require("vm");

const [appPath, responsesPath] = process.argv.slice(2);
const responses = JSON.parse(fs.readFileSync(responsesPath, "utf8"));

const unhandled = [];
process.on("unhandledRejection", (err) => unhandled.push(String(err && err.stack || err)));

// Mindent elfogado ertek: barmely tulajdonsag/hivas ujabb ilyen erteket ad
function universal() {
  const store = {};
  const target = function () {};
  return new Proxy(target, {
    get(_t, prop) {
      if (prop === Symbol.toPrimitive) return () => "";
      if (prop === Symbol.iterator) return function* () {};
      if (prop === "then") return undefined; // ne legyen "thenable"
      if (prop in store) return store[prop];
      const child = universal();
      store[prop] = child;
      return child;
    },
    set(_t, prop, value) {
      store[prop] = value;
      return true;
    },
    apply() {
      return universal();
    },
  });
}

// Egyszeru <select> helyettes: a children es a value valoban mukodik
function makeSelect() {
  const sel = universal();
  const children = [];
  let value = "";
  return new Proxy(sel, {
    get(t, prop) {
      if (prop === "children" || prop === "options") return children;
      if (prop === "value") return value;
      if (prop === "appendChild") return (el) => { children.push(el); return el; };
      return t[prop];
    },
    set(t, prop, v) {
      if (prop === "innerHTML") { children.length = 0; return true; }
      if (prop === "value") { value = v === undefined || v === null ? "" : String(v); return true; }
      t[prop] = v;
      return true;
    },
  });
}

const elements = {};
const document = {
  getElementById(id) {
    if (!elements[id]) elements[id] = id.endsWith("Select") ? makeSelect() : universal();
    return elements[id];
  },
  querySelector: () => universal(),
  querySelectorAll: () => [],
  createElement: () => ({}),
  addEventListener: () => {},
  activeElement: null,
};

const fetched = [];
async function fetch(path) {
  fetched.push(path);
  const body = Object.prototype.hasOwnProperty.call(responses, path) ? responses[path] : {};
  return { ok: true, status: 200, json: async () => body };
}

const sandbox = {
  document,
  fetch,
  console: { log() {}, warn() {}, error() {} },
  window: { location: { search: "", pathname: "/" }, history: { replaceState() {} } },
  URLSearchParams,
  // az idozitett lekerdezesek ne fussanak vegtelen ciklusban
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  FormData: function () {},
  Promise,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(appPath, "utf8"), sandbox, { filename: "app.js" });

setImmediate(function report() {
  // nehany esemenykor a promise-lancoknak
  let rounds = 0;
  (function wait() {
    if (rounds++ < 20) return setImmediate(wait);
    const select = elements["mixerInputSelect"];
    const options = select ? Array.from(select.children).map((o) => ({ text: o.textContent, disabled: !!o.disabled })) : [];
    process.stdout.write(JSON.stringify({ fetched, unhandled, inputOptions: options }));
  })();
});
