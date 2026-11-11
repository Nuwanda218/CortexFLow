/* Pure dispatch syntax and identity primitives, shared by extension contexts. */
(() => {
  "use strict";

  const OPEN = "[CONTEXTFLOW_DISPATCH_V1]";
  const CLOSE = "[/CONTEXTFLOW_DISPATCH_V1]";

  function normalizeNewlines(text) {
    if (typeof text !== "string") {
      throw new TypeError("text must be a string");
    }
    return text.replace(/\r\n?/g, "\n");
  }

  function normalizeFullMessage(text) {
    return normalizeNewlines(text).trim();
  }

  function invalid(code, message) {
    return { status: "invalid", blocks: [], content: null, error: { code, message } };
  }

  function parse(text) {
    const blocks = [];
    // A fence temporarily masks markers in either the outside or inside state.
    let body = null;
    let fence = null;

    for (const line of normalizeNewlines(text).split("\n")) {
      if (fence !== null) {
        if (body !== null) body.push(line);
        const closing = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
        if (closing && closing[1][0] === fence.character && closing[1].length >= fence.length) {
          fence = null;
        }
        continue;
      }

      const opening = /^ {0,3}(`{3,}|~{3,})([\s\S]*)$/.exec(line);
      // CommonMark disallows backticks in a backtick fence's info string.
      if (opening && (opening[1][0] === "~" || !opening[2].includes("`"))) {
        fence = { character: opening[1][0], length: opening[1].length };
        if (body !== null) body.push(line);
        continue;
      }

      const marker = line.trim();
      if ((line.includes(OPEN) || line.includes(CLOSE)) && marker !== OPEN && marker !== CLOSE) {
        return invalid("AMBIGUOUS_MARKER", "Dispatch markers must occupy a line by themselves.");
      }
      if (marker === OPEN) {
        if (body !== null) {
          return invalid("NESTED_OPEN_MARKER", "A dispatch block cannot contain another opening marker.");
        }
        body = [];
      } else if (marker === CLOSE) {
        if (body === null) {
          return invalid("ORPHAN_CLOSE_MARKER", "A closing dispatch marker has no matching opening marker.");
        }
        const content = normalizeFullMessage(body.join("\n"));
        if (!content) {
          return invalid("EMPTY_BLOCK", "A dispatch block must contain non-whitespace text.");
        }
        blocks.push(content);
        body = null;
      } else if (body !== null) {
        body.push(line);
      }
    }

    if (body !== null) {
      return invalid("UNCLOSED_BLOCK", "A dispatch block is missing its closing marker.");
    }
    return blocks.length
      ? { status: "valid", blocks, content: blocks.join("\n\n"), error: null }
      : { status: "none", blocks, content: null, error: null };
  }

  /**
   * Return canonical JSON, not an array. normalizedText must already be the
   * output of parse or normalizeFullMessage; identity values are not rewritten.
   * Trigger mode and auxiliary DOM evidence never participate in this document.
   */
  function canonicalIdentityDocument(input) {
    if (input === null || typeof input !== "object" || Array.isArray(input)) {
      throw new TypeError("identity must be an object");
    }
    const { endpointId, sessionId, messageId, normalizedText } = input;
    for (const [name, value] of Object.entries({ endpointId, sessionId, messageId, normalizedText })) {
      if (typeof value !== "string" || !value.trim()) {
        throw new TypeError(`${name} must be a nonempty string`);
      }
    }
    return JSON.stringify([
      "contextflow-supervisor-submission-v1",
      "chatgpt_web",
      endpointId,
      sessionId,
      messageId,
      "markdown",
      normalizedText,
    ]);
  }

  async function submissionIdFor(input) {
    const bytes = new TextEncoder().encode(canonicalIdentityDocument(input));
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    const hex = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `sub_${hex}`;
  }

  globalThis.ContextFlowDispatch = Object.freeze({
    parse,
    normalizeFullMessage,
    canonicalIdentityDocument,
    submissionIdFor,
  });
})();
