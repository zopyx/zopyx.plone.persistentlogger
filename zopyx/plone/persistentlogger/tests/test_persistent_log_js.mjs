import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import vm from "node:vm";

const SCRIPT = await readFile(
  new URL("../browser/resources/persistent-log.js", import.meta.url),
  "utf8",
);

class ClassList {
  #values = new Set();

  toggle(name, force) {
    if (force === undefined ? !this.#values.has(name) : force) {
      this.#values.add(name);
    } else {
      this.#values.delete(name);
    }
  }

  has(name) {
    return this.#values.has(name);
  }
}

class Element {
  constructor(id) {
    this.id = id;
    this.attributes = {};
    this.classList = new ClassList();
    this.hidden = false;
    this.textContent = "";
    this.value = "";
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  dispatch(type) {
    this.listeners.get(type)?.({ target: this });
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

function runtime({ responses }) {
  const elements = new Map(
    [
      "persistent-log-grid",
      "persistent-log-grid-region",
      "persistent-log-status",
      "persistent-log-quick",
      "persistent-log-reset",
      "persistent-log-retry",
    ].map((id) => [id, new Element(id)]),
  );
  const document = {
    activeElement: null,
    readyState: "complete",
    getElementById(id) {
      return elements.get(id) ?? null;
    },
  };
  for (const element of elements.values()) {
    element.ownerDocument = document;
  }

  const requests = [];
  const fetchUrls = [];
  const api = {
    purged: 0,
    shownNoRows: 0,
    hiddenOverlay: 0,
    paginationGoToPage() {},
    setFilterModel() {},
    applyColumnState() {},
    purgeInfiniteCache() {
      this.purged += 1;
      queueRows();
    },
    showNoRowsOverlay() {
      this.shownNoRows += 1;
    },
    hideOverlay() {
      this.hiddenOverlay += 1;
    },
  };
  let options;
  let callbacks;

  function queueRows() {
    setTimeout(() => {
      const params = {
        api,
        startRow: 0,
        endRow: 25,
        sortModel: [],
        filterModel: {},
        successCallback(rows, lastRow) {
          callbacks = { rows, lastRow };
        },
        failCallback() {
          callbacks = { failed: true };
        },
      };
      requests.push(params);
      options.datasource.getRows(params);
    }, 0);
  }

  const agGrid = {
    createGrid(_element, gridOptions) {
      options = gridOptions;
      queueRows();
      return api;
    },
  };
  const sandbox = {
    document,
    fetch: async (url) => {
      fetchUrls.push(url);
      const response = responses.shift();
      if (response instanceof Error) {
        throw response;
      }
      return {
        ok: response.ok,
        json: async () => response.data,
      };
    },
    agGrid,
    window: {
      PERSISTENT_LOGGER_CONFIG: {
        dataUrl: "https://example.test/@@persistent-log-data",
        columns: [
          { field: "comment", headerName: "Comment", kind: "text" },
        ],
        pageSize: 25,
        cacheBlockSize: 1000,
      },
      location: { origin: "https://example.test" },
      setTimeout,
      clearTimeout,
    },
    URL,
    console,
    setTimeout,
    clearTimeout,
    Promise,
  };
  vm.runInNewContext(SCRIPT, sandbox);

  return {
    api,
    callbacks: () => callbacks,
    elements,
    options: () => options,
    requests,
    fetchUrls,
  };
}

function settled() {
  return new Promise((resolve) => setTimeout(resolve, 10));
}

test("loads one bounded grid page and announces the result", async () => {
  const state = runtime({
    responses: [
      {
        ok: true,
        data: { rows: [{ comment: "entry" }], total: 1, lastRow: 1 },
      },
    ],
  });
  await settled();

  const url = new URL(state.fetchUrls[0]);
  assert.equal(state.options().cacheBlockSize, 100);
  assert.equal(state.options().maxBlocksInCache, 3);
  assert.equal(state.options().paginationPageSize, 25);
  assert.equal(state.callbacks().lastRow, 1);
  assert.deepEqual(state.callbacks().rows, [{ comment: "entry" }]);
  assert.equal(state.elements.get("persistent-log-status").textContent, "1 entries");
  assert.equal(
    state.elements.get("persistent-log-grid-region").attributes["aria-busy"],
    "false",
  );
  assert.equal(state.api.hiddenOverlay, 1);
  assert.equal(url.pathname, "/@@persistent-log-data");
  assert.equal(url.searchParams.get("startRow"), "0");
  assert.equal(url.searchParams.get("endRow"), "25");
});

test("announces errors, exposes retry, and returns focus to the grid region", async () => {
  const state = runtime({ responses: [new Error("offline"), {
    ok: true,
    data: { rows: [], total: 0, lastRow: 0 },
  }] });
  await settled();

  const status = state.elements.get("persistent-log-status");
  const retry = state.elements.get("persistent-log-retry");
  assert.equal(status.textContent, "Could not load entries. Try again.");
  assert.equal(status.attributes.role, "alert");
  assert.equal(status.attributes["aria-live"], "assertive");
  assert.equal(retry.hidden, false);

  retry.dispatch("click");
  assert.equal(state.elements.get("persistent-log-grid-region").ownerDocument.activeElement, state.elements.get("persistent-log-grid-region"));
  await settled();
  assert.equal(status.textContent, "No entries found.");
  assert.equal(status.attributes.role, "status");
  assert.equal(state.api.shownNoRows, 1);
});
