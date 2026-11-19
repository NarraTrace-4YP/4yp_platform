# Vendored libraries

The site's security settings (the Caddyfile) only allow scripts from the site itself, so
libraries are stored here rather than loaded from a CDN.

## three.js

`three.min.js` is three.js r187 (MIT licence, `three-LICENSE.txt`), cut down to the classes
`../story.js` imports and minified. To rebuild it, for example after adding an import to
`story.js`, add the class to `three-entry.js` and run, from an empty folder:

    npm install three@0.187.0 esbuild@0.25
    npx esbuild /path/to/site/assets/vendor/three-entry.js --bundle --format=esm \
      --minify --legal-comments=eof --outfile=/path/to/site/assets/vendor/three.min.js

then put back the one-line comment at the top of `three.min.js`.
