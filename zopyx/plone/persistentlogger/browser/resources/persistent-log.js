/*
 * agGrid based entries view of zopyx.plone.persistentlogger.
 *
 * The grid runs in infinite row model mode: paging, sorting and filtering are
 * executed by the server (see @@persistent-log-data), so the browser never
 * holds more than one page of entries.  Everything the script needs is
 * rendered into window.PERSISTENT_LOGGER_CONFIG by the page template.
 */
(function () {
    "use strict";

    var GRID_ID = "persistent-log-grid";
    var GRID_REGION_ID = "persistent-log-grid-region";
    var STATUS_ID = "persistent-log-status";
    var QUICK_ID = "persistent-log-quick";

    function escapeHtml(value) {
        return String(value === null || value === undefined ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function filterTypeFor(kind) {
        if (kind === "number") {
            return "agNumberColumnFilter";
        }
        if (kind === "date") {
            return "agDateColumnFilter";
        }
        return "agTextColumnFilter";
    }

    function detailsRenderer(params) {
        if (!params.value) {
            return "";
        }
        return (
            '<pre class="mb-0 small text-body-secondary">' +
            escapeHtml(JSON.stringify(params.value, null, 1)) +
            "</pre>"
        );
    }

    function severityRenderer(params) {
        var classes = {
            debug: "text-bg-secondary",
            info: "text-bg-info",
            warning: "text-bg-warning",
            error: "text-bg-danger",
            critical: "text-bg-danger"
        };
        var css = classes[params.value] || "text-bg-light";
        return '<span class="badge ' + css + '">' + escapeHtml(params.value) + "</span>";
    }

    function infoRenderer(params) {
        if (!params.value) {
            return "";
        }
        var url = String(params.value);
        if (url.indexOf("http") !== 0) {
            url = window.location.origin + (url.charAt(0) === "/" ? "" : "/") + url;
        }
        return '<a href="' + escapeHtml(url) + '" aria-label="Open event information">Info</a>';
    }

    function buildColumnDefs(columns) {
        return columns.map(function (column) {
            var definition = {
                field: column.field,
                headerName: column.headerName,
                hide: !!column.hide,
                sortable: column.sortable !== false,
                filter: column.filterable === false ? false : filterTypeFor(column.kind),
                resizable: true,
                minWidth: column.kind === "date" ? 180 : 120
            };
            if (column.field === "created_at") {
                definition.valueFormatter = function (params) {
                    return params.value ? new Date(params.value).toLocaleString() : "";
                };
                definition.filterParams = {
                    comparator: function (filterDate, cellValue) {
                        if (!cellValue) {
                            return -1;
                        }
                        return new Date(cellValue).getTime() - filterDate.getTime();
                    }
                };
            } else if (column.field === "comment") {
                definition.flex = 3;
                // wrapText only: agGrid's infinite row model ignores autoHeight
                // and logs a console warning, so the row height stays uniform.
                definition.wrapText = true;
            } else if (column.field === "details") {
                definition.flex = 2;
                definition.cellRenderer = detailsRenderer;
            } else if (column.field === "severity") {
                definition.cellRenderer = severityRenderer;
                definition.maxWidth = 140;
            } else if (column.field === "info_url") {
                definition.cellRenderer = infoRenderer;
                definition.maxWidth = 90;
            }
            return definition;
        });
    }

    function boundedNumber(value, fallback, minimum, maximum) {
        var number = Number(value);
        if (!Number.isFinite(number)) {
            return fallback;
        }
        return Math.min(Math.max(Math.round(number), minimum), maximum);
    }

    function gridOptions(config, status, retry, gridRegion) {
        var cacheBlockSize = boundedNumber(config.cacheBlockSize, 100, 1, 100);
        var pageSize = boundedNumber(config.pageSize, 25, 10, 100);
        return {
            columnDefs: buildColumnDefs(config.columns),
            rowModelType: "infinite",
            cacheBlockSize: cacheBlockSize,
            cacheOverflowSize: 2,
            maxBlocksInCache: 3,
            maxConcurrentDatasourceRequests: 2,
            infiniteInitialRowCount: 1,
            pagination: true,
            paginationPageSize: pageSize,
            paginationPageSizeSelector: [10, 25, 50, 100],
            ensureDomOrder: true,
            overlayNoRowsTemplate:
                '<span class="ag-overlay-no-rows" role="status">No entries found.</span>',
            defaultColDef: {
                sortable: true,
                resizable: true,
                filter: true,
                flex: 1
            },
            datasource: {
                getRows: function (params) {
                    loadRows(params, config, status, retry, gridRegion);
                }
            }
        };
    }

    function setRetryVisible(retry, visible) {
        if (retry) {
            retry.hidden = !visible;
        }
    }

    function loadRows(params, config, status, retry, gridRegion) {
        var url = new URL(config.dataUrl, window.location.origin);
        var quick = document.getElementById(QUICK_ID);
        url.searchParams.set("startRow", params.startRow);
        url.searchParams.set("endRow", params.endRow);
        url.searchParams.set("sortModel", JSON.stringify(params.sortModel || []));
        url.searchParams.set("filterModel", JSON.stringify(params.filterModel || {}));
        if (quick && quick.value) {
            url.searchParams.set("quick", quick.value);
        }
        setRetryVisible(retry, false);
        setGridBusy(gridRegion, true);
        showStatus(status, "Loading entries…", false);
        fetch(url.toString(), {
            headers: { Accept: "application/json" },
            credentials: "same-origin"
        })
            .then(function (response) {
                return response.json().then(function (data) {
                    return { ok: response.ok, data: data };
                });
            })
            .then(function (result) {
                if (!result.ok || result.data.error) {
                    setGridBusy(gridRegion, false);
                    showStatus(status, "Could not load entries. Try again.", true);
                    setRetryVisible(retry, true);
                    if (typeof params.failCallback === "function") {
                        params.failCallback();
                    }
                    return;
                }
                setGridBusy(gridRegion, false);
                showStatus(
                    status,
                    result.data.total === 0
                        ? "No entries found."
                        : result.data.total + " entries",
                    false
                );
                setRetryVisible(retry, false);
                if (params.api) {
                    if (result.data.total === 0 && params.api.showNoRowsOverlay) {
                        params.api.showNoRowsOverlay();
                    } else if (params.api.hideOverlay) {
                        params.api.hideOverlay();
                    }
                }
                // Infinite row model API of agGrid 32: successCallback(rows,
                // lastRow).  The *server side* row model's success/fail
                // callbacks do not exist here and throw at runtime.
                params.successCallback(result.data.rows, result.data.lastRow);
            })
            .catch(function () {
                setGridBusy(gridRegion, false);
                showStatus(status, "Could not load entries. Try again.", true);
                setRetryVisible(retry, true);
                if (typeof params.failCallback === "function") {
                    params.failCallback();
                }
            });
    }

    function setGridBusy(gridRegion, busy) {
        if (gridRegion) {
            gridRegion.setAttribute("aria-busy", busy ? "true" : "false");
        }
    }

    function showStatus(status, text, isError) {
        if (!status) {
            return;
        }
        status.textContent = text;
        status.setAttribute("role", isError ? "alert" : "status");
        status.setAttribute("aria-live", isError ? "assertive" : "polite");
        status.classList.toggle("text-danger", !!isError);
        status.classList.toggle("text-body-secondary", !isError);
    }

    function debounce(callback, delay) {
        var handle = null;
        return function () {
            window.clearTimeout(handle);
            handle = window.setTimeout(callback, delay);
        };
    }

    function start() {
        var config = window.PERSISTENT_LOGGER_CONFIG;
        var element = document.getElementById(GRID_ID);
        if (!config || !element || typeof agGrid === "undefined") {
            return;
        }
        var status = document.getElementById(STATUS_ID);
        var retry = document.getElementById("persistent-log-retry");
        var gridRegion = document.getElementById(GRID_REGION_ID) || element;
        setGridBusy(gridRegion, true);
        var gridApi = agGrid.createGrid(
            element,
            gridOptions(config, status, retry, gridRegion)
        );

        if (retry) {
            retry.addEventListener("click", function () {
                setRetryVisible(retry, false);
                if (gridRegion && typeof gridRegion.focus === "function") {
                    gridRegion.focus({ preventScroll: true });
                }
                gridApi.purgeInfiniteCache();
            });
        }

        var quick = document.getElementById(QUICK_ID);
        if (quick) {
            quick.addEventListener(
                "input",
                debounce(function () {
                    gridApi.paginationGoToPage(0);
                    gridApi.purgeInfiniteCache();
                }, 250)
            );
        }
        var reset = document.getElementById("persistent-log-reset");
        if (reset) {
            reset.addEventListener("click", function () {
                if (quick) {
                    quick.value = "";
                }
                gridApi.setFilterModel(null);
                gridApi.applyColumnState({
                    defaultState: { sort: null },
                    state: [{ colId: "created_at", sort: "desc" }]
                });
                gridApi.paginationGoToPage(0);
                gridApi.purgeInfiniteCache();
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
