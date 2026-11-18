import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

const fontBase = new URL('/brand-assets/fonts/', window.location.href)
const brandFonts = document.createElement('style')
brandFonts.textContent = `
  @font-face {
    font-family: 'Archivo';
    font-weight: 400;
    font-display: swap;
    src: url('${new URL('archivo-latin-400-normal.woff2', fontBase)}') format('woff2');
  }
  @font-face {
    font-family: 'Archivo';
    font-weight: 700;
    font-display: swap;
    src: url('${new URL('archivo-latin-700-normal.woff2', fontBase)}') format('woff2');
  }
  @font-face {
    font-family: 'IBM Plex Mono';
    font-weight: 500;
    font-display: swap;
    src: url('${new URL('ibm-plex-mono-latin-500-normal.woff2', fontBase)}') format('woff2');
  }
  @font-face {
    font-family: 'IBM Plex Mono';
    font-weight: 700;
    font-display: swap;
    src: url('${new URL('ibm-plex-mono-latin-700-normal.woff2', fontBase)}') format('woff2');
  }
`
document.head.append(brandFonts)

createRoot(document.getElementById('root')!).render(<App />)
