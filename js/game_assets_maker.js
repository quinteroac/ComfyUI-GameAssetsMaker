import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SPINE_EXPORT_NODE = "GameAssets_RigToSpineExport";
const ASEPRITE_ATLAS_NODE = "GameAssets_AsepriteVisualNovelAtlas";

function filenameFromDisposition(disposition, fallback) {
    const match = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(disposition || "");
    const value = match?.[1] || match?.[2];
    return value ? decodeURIComponent(value) : fallback;
}

function rememberExport(node, output) {
    const payload = output?.game_assets_spine?.[0] || output?.game_assets_spine;
    if (payload?.export_dir) {
        node.gameAssetsMakerSpineExport = payload;
    }
}

async function downloadLatestExport(node) {
    const exportInfo = node.gameAssetsMakerSpineExport;
    if (!exportInfo?.export_dir) {
        app.extensionManager?.toast?.add?.({
            severity: "warn",
            summary: "Spine export",
            detail: "Execute the node before downloading.",
            life: 3000,
        });
        return;
    }

    const response = await api.fetchApi(
        `/game_assets_maker/download_spine?dir=${encodeURIComponent(exportInfo.export_dir)}`
    );
    if (!response.ok) {
        let message = `Download failed (${response.status})`;
        try {
            const data = await response.json();
            message = data.error || message;
        } catch (_error) {
            // Keep the status-based message when the response is not JSON.
        }
        throw new Error(message);
    }

    const blob = await response.blob();
    const filename = filenameFromDisposition(
        response.headers.get("content-disposition"),
        `${exportInfo.asset_name || "spine_export"}.zip`
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

function addDownloadButton(node) {
    if (node.gameAssetsMakerDownloadButton) {
        return;
    }
    node.gameAssetsMakerDownloadButton = node.addWidget("button", "Download Spine ZIP", null, async () => {
        try {
            await downloadLatestExport(node);
        } catch (error) {
            app.extensionManager?.toast?.add?.({
                severity: "error",
                summary: "Spine export",
                detail: error?.message || "Could not download Spine export.",
                life: 5000,
            });
            console.error("[GameAssetsMaker] Spine ZIP download failed", error);
        }
    });
}

function findWidget(node, name) {
    return node.widgets?.find((widget) => widget.name === name);
}

function spriteNameWidgets(node) {
    return (node.widgets || []).filter((widget) => widget.name?.startsWith("sprite_") && widget.name?.endsWith("_name"));
}

function parseSpriteNames(value, count) {
    let names = [];
    const text = String(value || "").trim();
    if (text) {
        try {
            const parsed = JSON.parse(text);
            if (Array.isArray(parsed)) {
                names = parsed.map((item) => String(item || ""));
            }
        } catch (_error) {
            names = text.split(/\r?\n/);
        }
    }
    while (names.length < count) {
        names.push(`sprite_${String(names.length + 1).padStart(2, "0")}`);
    }
    return names.slice(0, count);
}

function spriteCount(node) {
    const countWidget = findWidget(node, "sprite_count");
    const count = Number.parseInt(countWidget?.value, 10);
    return Number.isFinite(count) ? Math.max(1, Math.min(128, count)) : 1;
}

function syncSpriteNamesToHiddenWidget(node) {
    const hiddenWidget = findWidget(node, "sprite_names");
    if (!hiddenWidget) {
        return;
    }
    const names = spriteNameWidgets(node)
        .sort((a, b) => Number(a.gameAssetsMakerSpriteIndex) - Number(b.gameAssetsMakerSpriteIndex))
        .map((widget, index) => String(widget.value || `sprite_${String(index + 1).padStart(2, "0")}`).trim());
    hiddenWidget.value = JSON.stringify(names);
}

function hideRawSpriteNamesWidget(node) {
    const widget = findWidget(node, "sprite_names");
    if (!widget || widget.gameAssetsMakerHidden) {
        return;
    }
    const originalSerializeValue = widget.serializeValue?.bind(widget);
    widget.gameAssetsMakerHidden = true;
    widget.hidden = true;
    widget.computeSize = () => [0, -4];
    widget.serializeValue = () => {
        syncSpriteNamesToHiddenWidget(node);
        return originalSerializeValue ? originalSerializeValue() : widget.value;
    };
    for (const element of [widget.element, widget.inputEl, widget.textElement]) {
        if (element?.style) {
            element.style.display = "none";
            element.style.height = "0";
            element.style.minHeight = "0";
            element.style.overflow = "hidden";
        }
    }
}

function removeSpriteNameWidgets(node) {
    for (const widget of spriteNameWidgets(node)) {
        if (typeof node.removeWidget === "function") {
            node.removeWidget(widget);
        } else {
            const index = node.widgets.indexOf(widget);
            if (index >= 0) {
                node.widgets.splice(index, 1);
            }
        }
    }
}

function rebuildSpriteNameWidgets(node) {
    const count = spriteCount(node);
    const hiddenWidget = findWidget(node, "sprite_names");
    const existingNames = parseSpriteNames(hiddenWidget?.value, count);

    removeSpriteNameWidgets(node);
    for (let index = 0; index < count; index += 1) {
        const widget = node.addWidget("text", `sprite_${index + 1}_name`, existingNames[index], () => {
            syncSpriteNamesToHiddenWidget(node);
        });
        widget.gameAssetsMakerSpriteIndex = index;
        widget.beforeQueued = () => syncSpriteNamesToHiddenWidget(node);
    }

    syncSpriteNamesToHiddenWidget(node);
    node.setSize?.(node.computeSize?.());
    app.graph?.setDirtyCanvas?.(true, true);
}

function setupAsepriteAtlasNode(node) {
    if (node.gameAssetsMakerAsepriteSetup) {
        rebuildSpriteNameWidgets(node);
        return;
    }
    node.gameAssetsMakerAsepriteSetup = true;
    hideRawSpriteNamesWidget(node);

    const countWidget = findWidget(node, "sprite_count");
    if (countWidget && !countWidget.gameAssetsMakerWrapped) {
        const originalCallback = countWidget.callback;
        countWidget.callback = function () {
            originalCallback?.apply(this, arguments);
            rebuildSpriteNameWidgets(node);
        };
        countWidget.gameAssetsMakerWrapped = true;
    }

    rebuildSpriteNameWidgets(node);
}

app.registerExtension({
    name: "ComfyUI.GameAssetsMaker",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === SPINE_EXPORT_NODE) {
            const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                originalOnNodeCreated?.apply(this, arguments);
                addDownloadButton(this);
            };

            const originalOnExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (output) {
                originalOnExecuted?.apply(this, arguments);
                rememberExport(this, output);
            };
        }

        if (nodeData.name === ASEPRITE_ATLAS_NODE) {
            const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                originalOnNodeCreated?.apply(this, arguments);
                setupAsepriteAtlasNode(this);
            };

            const originalOnSerialize = nodeType.prototype.onSerialize;
            nodeType.prototype.onSerialize = function () {
                syncSpriteNamesToHiddenWidget(this);
                return originalOnSerialize?.apply(this, arguments);
            };
        }
    },

    loadedGraphNode(node) {
        if (node.comfyClass === SPINE_EXPORT_NODE) {
            addDownloadButton(node);
        }
        if (node.comfyClass === ASEPRITE_ATLAS_NODE) {
            setupAsepriteAtlasNode(node);
        }
    },
});
