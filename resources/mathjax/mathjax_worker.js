/**
 * Headless MathJax Daemon Worker (TICK-008).
 *
 * Runs headlessly under Node.js 22.23.2 LTS with pinned mathjax-full@3.2.2.
 * Converts TeX/LaTeX formulas to self-contained standalone SVG with embedded vector glyphs.
 * Communicates via line-delimited JSON-RPC 2.0 over stdin/stdout.
 */

'use strict';

const readline = require('readline');
const { mathjax } = require('mathjax-full/js/mathjax.js');
const { TeX } = require('mathjax-full/js/input/tex.js');
const { SVG } = require('mathjax-full/js/output/svg.js');
const { liteAdaptor } = require('mathjax-full/js/adaptors/liteAdaptor.js');
const { RegisterHTMLHandler } = require('mathjax-full/js/handlers/html.js');
const { AllPackages } = require('mathjax-full/js/input/tex/AllPackages.js');

// Bounded buffer limits per Wayfinder v2.1.0 Section 9.3
const MAX_TEX_LENGTH = 16 * 1024;       // 16 KB
const MAX_SVG_LENGTH = 512 * 1024;      // 512 KB

// JSON-RPC 2.0 error codes
const PARSE_ERROR = -32700;
const INVALID_REQUEST = -32600;
const METHOD_NOT_FOUND = -32601;
const INVALID_PARAMS = -32602;
const INTERNAL_ERROR = -32603;

// Configure adaptor and HTML handler for headless operation (zero browser/DOM dependency)
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);

// Exclude 'noerrors' and 'noundefined' so syntax errors and undefined macros are trapped
const packages = AllPackages.filter(pkg => pkg !== 'noerrors' && pkg !== 'noundefined');

// Configure TeX input jax
const texJax = new TeX({
  packages: packages,
  formatError: (jax, err) => {
    throw err;
  }
});

// Configure SVG output jax with fontCache: 'local' for self-contained vector glyphs
const svgJax = new SVG({
  fontCache: 'local'
});

// Create headless MathJax document
const mathDoc = mathjax.document('', {
  InputJax: texJax,
  OutputJax: svgJax
});

/**
 * Render a TeX math formula into a self-contained SVG string and layout metrics.
 *
 * @param {string} tex - TeX/LaTeX formula string.
 * @param {boolean} display - True for display equation ($$...$$), false for inline ($...$).
 * @param {number} [em=16] - Em size in pixels.
 * @param {number} [ex=8] - Ex size in pixels.
 * @returns {{ svg: string, svg_xml: string, width: string, height: string, vertical_align: string }}
 */
function renderMath(tex, display = false, em = 16, ex = 8) {
  if (typeof tex !== 'string') {
    throw new Error("Invalid parameter: 'tex' must be a string");
  }
  const texByteLength = Buffer.byteLength(tex, 'utf8');
  if (texByteLength > MAX_TEX_LENGTH) {
    const err = new Error(`Buffer limit exceeded: TeX length (${texByteLength} bytes) exceeds ${MAX_TEX_LENGTH} bytes`);
    err.code = INVALID_REQUEST;
    throw err;
  }

  // Perform headless conversion
  const mathNode = mathDoc.convert(tex, {
    display: Boolean(display),
    em: Number(em) || 16,
    ex: Number(ex) || 8
  });

  const svgNode = adaptor.firstChild(mathNode);
  if (!svgNode || adaptor.kind(svgNode) !== 'svg') {
    throw new Error('MathJax conversion failed to produce an SVG element');
  }

  const svgXml = adaptor.outerHTML(svgNode);
  const svgByteLength = Buffer.byteLength(svgXml, 'utf8');
  if (svgByteLength > MAX_SVG_LENGTH) {
    const err = new Error(`Buffer limit exceeded: SVG output (${svgByteLength} bytes) exceeds ${MAX_SVG_LENGTH} bytes`);
    err.code = INVALID_REQUEST;
    throw err;
  }

  const width = adaptor.getAttribute(svgNode, 'width') || '0ex';
  const height = adaptor.getAttribute(svgNode, 'height') || '0ex';
  const verticalAlign = adaptor.getStyle(svgNode, 'vertical-align') || '0ex';

  return {
    svg: svgXml,
    svg_xml: svgXml,
    width: width,
    height: height,
    vertical_align: verticalAlign
  };
}

/**
 * Handle a single input line containing JSON-RPC 2.0 or direct request.
 *
 * @param {string} line - Raw input line from stdin.
 * @returns {object|null} Response object to serialize to stdout.
 */
function handleLine(line) {
  const trimmed = line.trim();
  if (!trimmed) {
    return null;
  }

  let req;
  try {
    req = JSON.parse(trimmed);
  } catch (parseErr) {
    return {
      jsonrpc: '2.0',
      id: null,
      error: {
        code: PARSE_ERROR,
        message: `Parse error: invalid JSON: ${parseErr.message}`
      }
    };
  }

  if (typeof req !== 'object' || req === null || Array.isArray(req)) {
    return {
      jsonrpc: '2.0',
      id: null,
      error: {
        code: INVALID_REQUEST,
        message: 'Invalid request: expected a JSON object'
      }
    };
  }

  const id = req.id !== undefined ? req.id : null;
  const method = req.method;

  // Diagnostic / capability probe methods
  if (method === 'ping') {
    return {
      jsonrpc: '2.0',
      id: id,
      result: 'pong'
    };
  }
  if (method === 'version') {
    return {
      jsonrpc: '2.0',
      id: id,
      result: {
        node: process.versions.node,
        mathjax: '3.2.2'
      }
    };
  }

  // Render method or direct request object
  if (method === 'render' || (!method && req.tex !== undefined)) {
    const params = req.params !== undefined ? req.params : req;
    if (typeof params !== 'object' || params === null) {
      return {
        jsonrpc: '2.0',
        id: id,
        error: {
          code: INVALID_PARAMS,
          message: "Invalid params: expected an object with 'tex'"
        }
      };
    }

    const tex = params.tex;
    if (typeof tex !== 'string') {
      return {
        jsonrpc: '2.0',
        id: id,
        error: {
          code: INVALID_PARAMS,
          message: "Invalid params: 'tex' string parameter is required"
        }
      };
    }

    const display = Boolean(params.display);
    const em = params.em !== undefined ? params.em : 16;
    const ex = params.ex !== undefined ? params.ex : 8;

    try {
      const renderResult = renderMath(tex, display, em, ex);
      return {
        jsonrpc: '2.0',
        id: id,
        result: renderResult
      };
    } catch (renderErr) {
      const code = renderErr.code !== undefined ? renderErr.code : INVALID_PARAMS;
      return {
        jsonrpc: '2.0',
        id: id,
        error: {
          code: code,
          message: renderErr.message || String(renderErr)
        }
      };
    }
  }

  // Unknown method
  return {
    jsonrpc: '2.0',
    id: id,
    error: {
      code: METHOD_NOT_FOUND,
      message: `Method not found: ${method}`
    }
  };
}

/**
 * Start the line-delimited stdio listener loop.
 */
function startWorker() {
  const rl = readline.createInterface({
    input: process.stdin,
    terminal: false
  });

  rl.on('line', (line) => {
    try {
      const response = handleLine(line);
      if (response !== null) {
        process.stdout.write(JSON.stringify(response) + '\n');
      }
    } catch (unexpectedErr) {
      const fallbackError = {
        jsonrpc: '2.0',
        id: null,
        error: {
          code: INTERNAL_ERROR,
          message: `Internal worker error: ${unexpectedErr.message}`
        }
      };
      process.stdout.write(JSON.stringify(fallbackError) + '\n');
    }
  });

  rl.on('close', () => {
    process.exit(0);
  });

  process.on('SIGINT', () => {
    process.exit(0);
  });

  process.on('SIGTERM', () => {
    process.exit(0);
  });
}

if (require.main === module) {
  startWorker();
}

module.exports = {
  renderMath,
  handleLine,
  startWorker,
  MAX_TEX_LENGTH,
  MAX_SVG_LENGTH
};
