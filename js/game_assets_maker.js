import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SPINE_EXPORT_NODE = "GameAssets_RigToSpineExport";

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

app.registerExtension({
    name: "ComfyUI.GameAssetsMaker.DownloadSpine",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== SPINE_EXPORT_NODE) {
            return;
        }

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
    },

    loadedGraphNode(node) {
        if (node.comfyClass === SPINE_EXPORT_NODE) {
            addDownloadButton(node);
        }
    },
});