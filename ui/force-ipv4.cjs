// Build-time network shim: force Node's dns.lookup to IPv4.
//
// Some Docker hosts advertise IPv6 routes that silently black-hole traffic;
// Node's fetch/undici then times out (e.g. next/font fetching Google Fonts
// during `next build`) even though IPv4 works. `--dns-result-order=ipv4first`
// does NOT fix that (undici's happy-eyeballs still attempts IPv6), so this
// pins the address family outright.
//
// Opt-in only: pass it through the Dockerfile's NODE_OPTIONS build arg, e.g.
//   --build-arg NODE_OPTIONS="--max-old-space-size=4096 --require /app/force-ipv4.cjs"
const dns = require("dns");
const origLookup = dns.lookup.bind(dns);
dns.lookup = (hostname, options, callback) => {
    if (typeof options === "function") {
        callback = options;
        options = {};
    }
    return origLookup(hostname, { ...options, family: 4 }, callback);
};
