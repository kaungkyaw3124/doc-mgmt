// njs handler for the web UI's index.html (see nginx.conf.template, "Static UI caching").
// The version is app.js's modification time, read on every request, so it
// changes as soon as a new app.js is on disk: no build step, no restart,
// nothing to bump by hand.
import fs from 'fs';

const ROOT = '/usr/share/nginx/html';

function mtime(path) {
    try {
        return Math.floor(fs.statSync(ROOT + path).mtimeMs).toString(36);
    } catch (e) {
        return '0';
    }
}

// Serves index.html with app.js's version written into its <script src>
// placeholder. The ETag covers index.html AND app.js (the body embeds the
// latter's version), so a 304 is only sent when the page really is the same.
function index(r) {
    const version = mtime('/js/app.js');
    const etag = '"' + mtime('/index.html') + '-' + version + '"';
    r.headersOut['ETag'] = etag;
    if (r.headersIn['If-None-Match'] === etag) {
        r.return(304);
        return;
    }
    const html = fs.readFileSync(ROOT + '/index.html', 'utf8');
    r.headersOut['Content-Type'] = 'text/html';
    r.return(200, html.replace('__APP_VERSION__', version));
}

export default { index };
