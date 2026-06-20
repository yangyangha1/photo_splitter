<template>
  <main class="app-shell">
    <header class="topbar" @dblclick="handleTopbarDoubleClick">
      <div class="brand window-drag">
        <img :src="appIconUrl" alt="" decoding="async" />
        <strong>照片分割器</strong>
      </div>
      <div class="topbar-center">
        <div class="topbar-drag-fill window-drag"></div>
        <div class="segmented-tabs">
          <button :class="{ active: tab === 'batch' }" @click="tab = 'batch'">批量处理</button>
          <button :class="{ active: tab === 'single' }" @click="tab = 'single'">单独处理</button>
        </div>
        <div class="topbar-drag-fill window-drag"></div>
      </div>
      <div class="topbar-right">
        <div class="topbar-drag-fill window-drag"></div>
        <div class="window-controls">
          <button title="最小化" @click="minimizeWindow">-</button>
          <button title="最大化/还原" @click="toggleWindowMaximize">□</button>
          <button class="close" title="关闭" @click="closeWindow">×</button>
        </div>
      </div>
    </header>

    <section v-if="tab === 'batch'" class="workspace">
      <aside class="left-pane">
        <PathPicker label="输入目录" v-model="batch.inputDir" @pick="pickDirectory('batchInput')" @commit="scanBatch" />
        <PathPicker label="输出目录" v-model="batch.outputDir" @pick="pickDirectory('batchOutput')" />
        <button class="secondary-action clear-action" :disabled="batch.processing" @click="clearBatch">清除选择</button>
        <ParameterPanel
          :config="config"
          :options="batch.options"
          @apply-preset="applyPreset(batch.options)"
          @parameter-change="logParameterChange(batch.logs, $event)"
        />
        <button class="primary-action main-action" :disabled="batch.processing" @click="detectBatch">{{ batchActionText }}</button>
        <p class="status-line">{{ batch.status }}</p>
      </aside>

      <PreviewPane title="批量源文件预览" :subtitle="batchPreviewSubtitle">
        <IntroContent v-if="!batch.items.length" mode="batch" />
        <div v-else-if="!batch.detected" class="file-groups">
          <section v-for="group in groupedBatchItems" :key="group.name" class="file-group">
            <h3>{{ group.name }}</h3>
            <div class="file-grid">
              <article v-for="item in group.items" :key="item.path" class="file-tile">
                <img v-if="item.thumb_url" class="file-thumb" :src="sourceThumbUrl(item)" alt="" loading="lazy" decoding="async" />
                <div v-else class="file-icon">IMG</div>
                <strong>{{ item.name }}</strong>
                <span>{{ item.relative || item.path }}</span>
              </article>
            </div>
          </section>
        </div>
        <div v-else class="batch-preview-content">
          <div ref="batchDetectionRef" class="file-groups batch-detection-groups">
            <section v-for="group in groupedBatchItems" :key="group.name" class="file-group">
              <h3>{{ group.name }}</h3>
              <div class="file-grid batch-detection-grid">
                <article
                  v-for="item in group.items"
                  :key="item.image_id || item.path"
                  class="file-tile detection-tile"
                  :data-batch-item-id="batchItemKey(item)"
                  @click="openBatchItem(item, $event)"
                >
                  <div class="detection-thumb">
                    <img v-if="item.thumb_url" class="file-thumb" :src="sourceThumbUrl(item)" alt="" loading="lazy" decoding="async" />
                    <div v-else class="file-icon">IMG</div>
                    <i v-for="(box, boxIndex) in item.boxes || []" :key="boxIndex" class="mini-box" :style="miniBoxStyle(item, box)"></i>
                  </div>
                  <strong>{{ item.name }}</strong>
                  <span>{{ item.relative || item.path }}</span>
                  <em :class="item.edited ? 'edited' : 'detected'">{{ item.edited ? "已修改" : "已检测" }} · 检测框 {{ item.box_count ?? item.boxes?.length ?? 0 }} 个</em>
                </article>
              </div>
            </section>
          </div>
          <div class="preview-footer batch-export-footer">
            <p class="status-line">{{ batch.status }}</p>
            <button class="primary-action small export-action" :disabled="batch.processing || !batch.items.length || !batch.outputDir.trim()" @click="exportBatch">确认导出</button>
          </div>
        </div>
      </PreviewPane>

      <aside class="right-pane">
        <PanelTitle title="处理队列" />
        <div ref="batchQueueRef" class="queue-card">
          <div v-for="item in batch.items" :key="item.path" class="queue-row" :class="queueTone(item.status)">
            <span>{{ item.name }}</span>
            <b>{{ item.status }}</b>
          </div>
          <p v-if="!batch.items.length" class="muted">未选择文件</p>
        </div>
        <PanelTitle title="处理进度" />
        <div class="stats">
          <StatBox label="已处理" :value="batch.done" tone="done" />
          <StatBox label="待处理" :value="batch.pending" tone="pending" />
          <StatBox label="输出" :value="batch.saved" tone="output" />
        </div>
        <PanelTitle title="处理日志" />
        <LogBox :logs="batch.logs" />
      </aside>

      <div v-if="batch.processing" class="process-shield">
        <div class="liquid-progress">
          <span>{{ batch.jobMode === "export" ? "批量导出中" : batch.detectMode === "redetect" ? "重新检测中" : "批量检测中" }}</span>
          <strong>{{ batch.status }}</strong>
          <div class="progress-track">
            <i :style="{ width: `${batchProgressPercent}%` }"></i>
          </div>
          <em>{{ batchProgressPercent }}%</em>
        </div>
      </div>
    </section>

    <section v-else class="workspace">
      <aside class="left-pane">
        <PathPicker label="单张照片" v-model="single.source" button-label="选择" @pick="pickFile" @commit="loadSinglePreview(single.source)" />
        <PathPicker label="输出目录" v-model="single.outputDir" @pick="pickDirectory('singleOutput')" />
        <button class="secondary-action clear-action" :disabled="single.processing" @click="clearSingle">清除选择</button>
        <ParameterPanel
          :config="config"
          :options="single.options"
          @apply-preset="applyPreset(single.options)"
          @parameter-change="logParameterChange(single.logs, $event)"
        />
        <button class="primary-action main-action" :disabled="single.processing" @click="detectSingle">
          {{ singleActionText }}
        </button>
      </aside>

      <PreviewPane
        :title="single.fromBatch ? '批量单图修正' : '单图检测预览'"
        :subtitle="singlePreviewSubtitle"
      >
        <IntroContent v-if="!single.imageUrl" mode="single" />
        <div v-else class="single-preview-content">
          <div
            ref="stageRef"
            class="image-stage"
            :class="{ zoomed: singleZoom > 1.01, panning: previewPan }"
            @wheel.prevent="zoomSinglePreview"
            @pointerdown="startPreviewPan"
          >
            <div class="image-layer" :style="imageLayerStyle">
              <img ref="imageRef" :src="single.imageUrl" alt="" decoding="async" @load="measureImage" />
              <div
                v-for="(box, index) in displayBoxes"
                :key="index"
                class="box"
                :class="{ selected: selectedBox === index }"
                :style="box.style"
                @pointerdown.stop="startDrag(index, 'move', $event)"
              >
                <span>{{ String(index + 1).padStart(3, '0') }}</span>
                <i v-for="handle in handles" :key="handle" :class="['handle', handle]" @pointerdown.stop="startDrag(index, handle, $event)" />
              </div>
            </div>
          </div>
          <div class="preview-footer">
            <p class="status-line">{{ single.status }}</p>
            <button v-if="single.fromBatch" class="primary-action small export-action" :disabled="single.processing" @click="returnToBatchPreview">保存并返回批量预览</button>
            <button v-else-if="single.boxes.length" class="primary-action small export-action" :disabled="single.processing" @click="exportSingle">确认导出</button>
          </div>
        </div>
      </PreviewPane>

      <aside class="right-pane">
        <PanelTitle title="检测框列表" />
        <div class="queue-card box-list-card">
          <div v-for="(box, index) in single.boxes" :key="index" class="queue-row done" :class="{ active: selectedBox === index }" @click="selectedBox = index">
            <span>{{ String(index + 1).padStart(3, '0') }}</span>
            <b>{{ Math.round(box[2] - box[0]) }} x {{ Math.round(box[3] - box[1]) }}</b>
          </div>
          <p v-if="!single.boxes.length" class="muted">暂无检测框</p>
        </div>
        <PanelTitle title="检测框操作" />
        <div class="box-action-card">
          <button :disabled="single.processing" @click="addBox">新增检测框</button>
          <button :disabled="single.processing" @click="deleteBox">删除选中</button>
          <button :disabled="single.processing" @click="splitBox('vertical')">纵向二等分</button>
          <button :disabled="single.processing" @click="splitBox('horizontal')">横向二等分</button>
          <button :disabled="single.processing" @click="undoBox">撤销</button>
        </div>
        <PanelTitle title="单图日志" />
        <LogBox :logs="single.logs" />
      </aside>

      <div v-if="single.processing" class="process-shield">
        <div class="liquid-progress indeterminate">
          <span>{{ single.progressTitle }}</span>
          <strong>{{ single.status }}</strong>
          <div class="progress-track"><i></i></div>
          <em>处理中</em>
        </div>
      </div>
    </section>

    <div v-if="modal" class="modal-mask">
      <div class="modal-card" :class="modal.kind || 'info'">
        <strong>{{ modal.title }}</strong>
        <p>{{ modal.message }}</p>
        <div class="modal-actions">
          <button v-if="modal.path" class="secondary-action modal-button" @click="openModalPath">打开目录</button>
          <button class="primary-action modal-button" @click="modal = null">关闭</button>
        </div>
      </div>
    </div>

    <div class="window-resize-zones" aria-hidden="true">
      <i v-for="edge in resizeEdges" :key="edge" :class="['window-resize-handle', edge]" :data-edge="edge" @pointerdown="startWindowResize" />
    </div>
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import IntroContent from "./components/IntroContent.vue";
import LogBox from "./components/LogBox.vue";
import PanelTitle from "./components/PanelTitle.vue";
import ParameterPanel from "./components/ParameterPanel.vue";
import PathPicker from "./components/PathPicker.vue";
import PreviewPane from "./components/PreviewPane.vue";
import StatBox from "./components/StatBox.vue";

const appIconUrl = "/photo_splitter_icon_preview.png";
const tab = ref("batch");
const batchQueueRef = ref(null);
const batchDetectionRef = ref(null);
const batchReturnTargetId = ref("");
const batchReturnScrollTop = ref(0);
const batchReturnOffsetTop = ref(0);
const config = reactive({
  presets: [
    { key: "balanced", name: "通用平衡", dark_threshold: 70, min_area_ratio: 0.002, white_threshold: 225, background_mode: "auto", skew_gain_percent: 4, detection_strategy: "balanced" },
    { key: "white_scan", name: "白底扫描件", dark_threshold: 68, min_area_ratio: 0.0016, white_threshold: 218, background_mode: "white", skew_gain_percent: 4, detection_strategy: "balanced" },
    { key: "dark_frame", name: "黑框/暗边相册", dark_threshold: 58, min_area_ratio: 0.0025, white_threshold: 240, background_mode: "black", skew_gain_percent: 5, detection_strategy: "balanced" },
    { key: "aggressive", name: "积极分割", dark_threshold: 78, min_area_ratio: 0.0012, white_threshold: 212, background_mode: "auto", skew_gain_percent: 3, detection_strategy: "aggressive" },
    { key: "conservative", name: "保守分割", dark_threshold: 62, min_area_ratio: 0.0035, white_threshold: 235, background_mode: "auto", skew_gain_percent: 7, detection_strategy: "conservative" },
  ],
  background_modes: [
    { key: "auto", label: "自动判断" },
    { key: "white", label: "白色/浅色底色" },
    { key: "gray", label: "灰色/杂色底色" },
    { key: "black", label: "黑色/深色底色" },
  ],
  default_preset: "balanced",
  jpeg_quality: 95,
});
const runtime = ref(null);
const modal = ref(null);
let runtimeDetectTimer = 0;

const defaultOptions = () => ({
  preset: "balanced",
  dark_threshold: 70,
  min_area_ratio: 0.002,
  white_threshold: 225,
  background_mode: "auto",
  skew_gain_percent: 4,
  detection_strategy: "balanced",
  split_strategy: "balanced",
  auto_face_rotate: false,
  save_split_preview: false,
});

const batch = reactive({
  inputDir: "",
  outputDir: "",
  items: [],
  options: defaultOptions(),
  status: "未扫描文件",
  logs: ["批量处理界面已就绪。"],
  jobId: "",
  serverLogCount: 0,
  done: 0,
  pending: 0,
  saved: 0,
  processing: false,
  detected: false,
  jobMode: "",
  detectMode: "full",
  progressTotal: 0,
  redetectTargetKeys: [],
  thumbToken: 0,
});

const single = reactive({
  source: "",
  outputDir: "",
  imageId: "",
  imageUrl: "",
  imageWidth: 1,
  imageHeight: 1,
  boxes: [],
  undo: [],
  options: defaultOptions(),
  status: "请选择单张照片",
  logs: ["单独处理界面已就绪。"],
  detected: false,
  processing: false,
  progressTitle: "正在处理",
  fromBatch: false,
  batchImageId: "",
});

const selectedBox = ref(null);
const stageRef = ref(null);
const imageRef = ref(null);
const singleZoom = ref(1);
const stage = reactive({ width: 1, height: 1, fitScale: 1 });
const drag = ref(null);
const previewPan = ref(null);
const handles = ["nw", "ne", "sw", "se", "n", "s", "w", "e"];
const resizeEdges = ["n", "s", "w", "e", "nw", "ne", "sw", "se"];
const windowResize = ref(null);

const batchProgressPercent = computed(() => {
  const total = batch.processing && batch.progressTotal ? batch.progressTotal : batch.items.length;
  if (!total) return 0;
  return Math.max(0, Math.min(100, Math.round((batch.done / total) * 100)));
});

const groupedBatchItems = computed(() => {
  const groups = new Map();
  batch.items.forEach((item) => {
    const relative = String(item.relative || item.name || "");
    const normalized = relative.replaceAll("\\", "/");
    const parts = normalized.split("/").filter(Boolean);
    const groupName = parts.length > 1 ? parts.slice(0, -1).join(" / ") : "根目录";
    if (!groups.has(groupName)) groups.set(groupName, []);
    groups.get(groupName).push(item);
  });
  return Array.from(groups, ([name, items]) => ({ name, items }));
});

const singleActionText = computed(() => {
  if (single.processing) return "检测中...";
  return single.detected ? "重新检测" : "检测并预览";
});

const batchActionText = computed(() => {
  if (batch.processing && batch.jobMode === "detect") {
    return batch.detectMode === "redetect" ? "重新检测中..." : "检测中...";
  }
  return batch.detected ? "重新检测" : "批量检测";
});

const batchPreviewSubtitle = computed(() => {
  if (!batch.items.length) return "选择目录后扫描 JPG / JPEG / PNG / TIF / TIFF";
  if (batch.detected) return `已检测 ${batch.items.length} 张图片，点击任意图片可放大修正检测框`;
  return `已选择 ${batch.items.length} 个文件`;
});

const singlePreviewSubtitle = computed(() => {
  if (single.fromBatch) {
    return single.boxes.length ? `批量修正中 · 检测框 ${single.boxes.length} 个` : "批量修正中，当前图片暂无检测框";
  }
  return single.boxes.length ? `检测框 ${single.boxes.length} 个` : single.imageUrl ? "已显示源图，点击检测并预览生成检测框" : "选择照片后检测并手动校正检测框";
});

watch(
  () => batch.items.map((item) => item.status).join("|"),
  async () => {
    await nextTick();
    scrollBatchQueueToRunning();
  },
  { flush: "post" },
);

const previewScale = computed(() => Math.max(0.001, stage.fitScale * singleZoom.value));

const imageLayerStyle = computed(() => ({
  width: `${Math.max(1, Math.round(single.imageWidth * previewScale.value))}px`,
  height: `${Math.max(1, Math.round(single.imageHeight * previewScale.value))}px`,
}));

const displayBoxes = computed(() =>
  single.boxes.map((box) => ({
    style: {
      left: `${box[0] * previewScale.value}px`,
      top: `${box[1] * previewScale.value}px`,
      width: `${(box[2] - box[0]) * previewScale.value}px`,
      height: `${(box[3] - box[1]) * previewScale.value}px`,
    },
  })),
);

function cloneOptions(options) {
  return JSON.parse(JSON.stringify(options || defaultOptions()));
}

function batchItemKey(item) {
  return item.image_id || `${item.path || ""}::${item.page_stem || ""}`;
}

function batchStableKey(item) {
  return `${item.path || ""}::${item.page_stem || ""}`;
}

function totalBatchBoxes(items = batch.items) {
  return items.reduce((sum, item) => sum + Number(item.box_count ?? item.boxes?.length ?? 0), 0);
}

function uniquePaths(items) {
  return Array.from(new Set(items.map((item) => item.path).filter(Boolean)));
}

function scrollBatchQueueToRunning() {
  const queue = batchQueueRef.value;
  if (!queue || queue.scrollHeight <= queue.clientHeight + 2) return;
  const runningRows = queue.querySelectorAll(".queue-row.running");
  const target = runningRows[runningRows.length - 1];
  if (!target) return;
  const top = target.offsetTop - queue.offsetTop - Math.max(0, (queue.clientHeight - target.clientHeight) / 2);
  queue.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
}

function waitForFrame() {
  return new Promise((resolve) => requestAnimationFrame(resolve));
}

function waitMs(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function scrollBatchPreviewToItem(itemId, restoreAnchor = false, highlight = false) {
  const scroller = batchDetectionRef.value;
  if (!scroller || !itemId) return false;
  const target = Array.from(scroller.querySelectorAll(".detection-tile")).find((tile) => tile.dataset.batchItemId === itemId);
  if (!target) return false;

  const scrollerRect = scroller.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  if (restoreAnchor) {
    const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    const desiredTop = scroller.scrollTop + targetRect.top - scrollerRect.top - batchReturnOffsetTop.value;
    scroller.scrollTop = Math.max(0, Math.min(maxTop, desiredTop));
  } else {
    const topGap = targetRect.top - scrollerRect.top;
    const bottomGap = targetRect.bottom - scrollerRect.bottom;
    if (topGap < 8) {
      scroller.scrollTop += topGap - 8;
    } else if (bottomGap > -8) {
      scroller.scrollTop += bottomGap + 8;
    }
  }
  if (highlight) {
    target.classList.add("return-highlight");
    window.setTimeout(() => target.classList.remove("return-highlight"), 900);
  }
  return true;
}

async function restoreBatchPreviewPosition(itemId) {
  await nextTick();
  let found = false;
  const delays = [0, 0, 40, 120, 260];
  for (const delay of delays) {
    if (delay) await waitMs(delay);
    await waitForFrame();
    found = scrollBatchPreviewToItem(itemId, true, false) || found;
  }
  if (found) {
    scrollBatchPreviewToItem(itemId, true, true);
  }
}

function miniBoxStyle(item, box) {
  const width = Math.max(1, Number(item.width || 1));
  const height = Math.max(1, Number(item.height || 1));
  return {
    left: `${(box[0] / width) * 100}%`,
    top: `${(box[1] / height) * 100}%`,
    width: `${((box[2] - box[0]) / width) * 100}%`,
    height: `${((box[3] - box[1]) / height) * 100}%`,
  };
}

function sourceThumbUrl(item) {
  const url = item?.thumb_url || "";
  if (!url) return "";
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}scan=${batch.thumbToken || Date.now()}`;
}

function cacheBustUrl(url, token = Date.now()) {
  const value = String(url || "");
  if (!value) return "";
  const separator = value.includes("?") ? "&" : "?";
  return `${value}${separator}t=${token}`;
}

async function api(path, body) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!data.ok) throw new Error(data.error || "请求失败");
  return data;
}

function pywebviewApi() {
  return window.pywebview?.api || null;
}

async function minimizeWindow() {
  await pywebviewApi()?.minimize();
}

async function toggleWindowMaximize() {
  await pywebviewApi()?.toggle_maximize();
}

async function handleTopbarDoubleClick(event) {
  if (event.target.closest("button, .segmented-tabs, .window-controls")) return;
  await toggleWindowMaximize();
}

async function closeWindow() {
  await pywebviewApi()?.close();
}

async function choosePath(kind, title) {
  const bridge = pywebviewApi();
  if (bridge?.select_path) {
    const selected = await bridge.select_path(kind, title);
    return selected || "";
  }
  throw new Error("当前运行环境没有原生路径选择接口，请手动输入路径。");
}

async function startWindowResize(event) {
  const bridge = pywebviewApi();
  const edge = event.currentTarget?.dataset?.edge;
  if (!bridge?.bounds || !bridge?.resize_window || !edge) return;
  event.preventDefault();
  event.stopPropagation();
  event.currentTarget.setPointerCapture?.(event.pointerId);
  const bounds = await bridge.bounds();
  if (bounds.maximized) return;
  windowResize.value = {
    edge,
    startX: event.screenX,
    startY: event.screenY,
    bounds,
    busy: false,
    pending: null,
  };
  window.addEventListener("pointermove", onWindowResizeMove);
  window.addEventListener("pointerup", stopWindowResize, { once: true });
  window.addEventListener("pointercancel", stopWindowResize, { once: true });
}

function onWindowResizeMove(event) {
  const state = windowResize.value;
  if (!state) return;
  state.pending = { x: event.screenX, y: event.screenY };
  if (state.busy) return;
  state.busy = true;
  window.requestAnimationFrame(applyWindowResize);
}

async function applyWindowResize() {
  const state = windowResize.value;
  if (!state?.pending) return;
  const point = state.pending;
  state.pending = null;
  const dx = point.x - state.startX;
  const dy = point.y - state.startY;
  const minWidth = 1400;
  const minHeight = 920;
  let x = state.bounds.x;
  let y = state.bounds.y;
  let width = state.bounds.width;
  let height = state.bounds.height;

  if (state.edge.includes("e")) width = Math.max(minWidth, state.bounds.width + dx);
  if (state.edge.includes("s")) height = Math.max(minHeight, state.bounds.height + dy);
  if (state.edge.includes("w")) {
    width = Math.max(minWidth, state.bounds.width - dx);
    x = state.bounds.x + (state.bounds.width - width);
  }
  if (state.edge.includes("n")) {
    height = Math.max(minHeight, state.bounds.height - dy);
    y = state.bounds.y + (state.bounds.height - height);
  }

  try {
    await pywebviewApi()?.resize_window({ x, y, width, height });
  } finally {
    if (windowResize.value) {
      windowResize.value.busy = false;
      if (windowResize.value.pending) {
        window.requestAnimationFrame(applyWindowResize);
      }
    }
  }
}

function stopWindowResize() {
  windowResize.value = null;
  window.removeEventListener("pointermove", onWindowResizeMove);
  window.removeEventListener("pointercancel", stopWindowResize);
}

function showModal(kind, title, message, path = "") {
  modal.value = { kind, title, message, path };
}

function showError(error) {
  showModal("error", "处理失败", error.message || String(error));
}

async function openModalPath() {
  if (!modal.value?.path) return;
  try {
    await api("/api/open-path", { path: modal.value.path });
  } catch (error) {
    showError(error);
  }
}

// 统一写日志，保留最近 300 条，避免长时间处理后页面变慢。
function pushLog(logs, line) {
  logs.push(line);
  if (logs.length > 300) logs.splice(0, logs.length - 300);
}

function logParameterChange(logs, event) {
  pushLog(logs, `参数调整：${event.name} -> ${event.value}`);
}

function escapeLogHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function applyPreset(options) {
  const preset = config.presets.find((item) => item.key === options.preset) || config.presets[0];
  if (!preset) return;
  options.dark_threshold = preset.dark_threshold;
  options.min_area_ratio = preset.min_area_ratio;
  options.white_threshold = preset.white_threshold;
  options.background_mode = preset.background_mode;
  options.skew_gain_percent = preset.skew_gain_percent;
  options.detection_strategy = preset.detection_strategy || preset.split_strategy || "balanced";
  options.split_strategy = options.detection_strategy;
}

function runtimeSummary(info) {
  if (!info) return "系统检测：正在检测运行环境。";
  const backendNames = {
    "cupy-cuda": "CuPy CUDA GPU",
    "opencv-cuda": "OpenCV CUDA GPU",
    "opencv-opencl": "OpenCV OpenCL 加速",
    "opencv-cpu": "OpenCV CPU",
    "numpy-cpu": "NumPy CPU",
    "pending-cuda": "检测时优先确认 CuPy CUDA",
    "pending-cpu": "检测时确认可用后端",
  };
  const cpu = info.cpu_name || `${info.cpu_count || 0} 线程 CPU`;
  const gpu = info.gpu_name || "未检测到独立 GPU";
  const backend = backendNames[info.compute_backend] || info.compute_backend;
  return {
    html: `检测到CPU：${escapeLogHtml(cpu)}；GPU：${escapeLogHtml(gpu)}；<br>当前使用算力：<b>${escapeLogHtml(backend)}</b>。`,
  };
}

async function loadRuntimeInfo() {
  try {
    const rt = await api("/api/runtime?probe=0");
    runtime.value = rt.runtime;
    const runtimeLine = runtimeSummary(runtime.value);
    pushLog(batch.logs, runtimeLine);
    pushLog(single.logs, runtimeLine);
  } catch (error) {
    pushLog(batch.logs, `系统检测失败：${error.message || error}`);
    pushLog(single.logs, `系统检测失败：${error.message || error}`);
  }
}

function queueTone(status) {
  if (status === "已检测") return "detected";
  if (status === "已修改") return "edited";
  if (status === "已完成") return "done";
  if (status === "处理中") return "running";
  if (status === "失败") return "failed";
  return "pending";
}

async function pickDirectory(target) {
  try {
    const path = await choosePath("directory", "选择目录");
    if (!path) return;
    if (target === "batchInput") {
      batch.inputDir = path;
      batch.outputDir ||= `${path}\\split_result`;
      pushLog(batch.logs, `选择输入目录：${path}`);
      await scanBatch();
    } else if (target === "batchOutput") {
      batch.outputDir = path;
      pushLog(batch.logs, `选择输出目录：${path}`);
    } else {
      single.outputDir = path;
      pushLog(single.logs, `选择输出目录：${path}`);
    }
  } catch (error) {
    showError(error);
  }
}

async function pickFile() {
  try {
    const path = await choosePath("file", "选择单张照片");
    if (!path) return;
    single.source = path;
    single.outputDir ||= path.replace(/\\[^\\]+$/, "\\split_result");
    single.fromBatch = false;
    single.batchImageId = "";
    pushLog(single.logs, `选择单张照片：${path}`);
    await loadSinglePreview(path);
  } catch (error) {
    showError(error);
  }
}

// 单图选择后先显示源图预览；检测完成后再替换为带检测框的处理图。
async function loadSinglePreview(path) {
  const source = String(path || "").trim();
  if (!source) return;
  try {
    const data = await api("/api/single/preview", { source });
    single.imageId = "";
    single.imageUrl = cacheBustUrl(data.image_url);
    single.imageWidth = data.width;
    single.imageHeight = data.height;
    single.boxes = [];
    single.undo = [];
    single.detected = false;
    singleZoom.value = 1;
    selectedBox.value = null;
    single.status = "已显示源图，点击检测并预览。";
    pushLog(single.logs, `源图预览已载入：${data.name}，尺寸 ${data.width}×${data.height}`);
    await nextTick();
    measureImage();
  } catch (error) {
    showError(error);
  }
}

async function scanBatch() {
  try {
    if (!batch.inputDir.trim()) throw new Error("输入目录为空。");
    const data = await api("/api/batch/scan", { input_dir: batch.inputDir, output_dir: batch.outputDir });
    batch.outputDir ||= data.output_dir;
    batch.items = data.items;
    batch.pending = data.count;
    batch.done = 0;
    batch.saved = 0;
    batch.detected = false;
    batch.jobMode = "";
    batch.detectMode = "full";
    batch.progressTotal = 0;
    batch.redetectTargetKeys = [];
    batch.thumbToken = Date.now();
    batch.status = `已扫描 ${data.count} 个文件`;
    pushLog(batch.logs, `扫描完成：${data.count} 个文件。`);
  } catch (error) {
    showError(error);
  }
}

async function detectBatch() {
  try {
    if (!batch.inputDir.trim()) throw new Error("输入目录为空。");
    if (!batch.items.length) await scanBatch();
    const isRedetect = batch.detected;
    const targetItems = isRedetect ? batch.items.filter((item) => !item.edited) : batch.items;
    const targetPaths = uniquePaths(targetItems);
    if (isRedetect && !targetPaths.length) {
      batch.status = "没有未修改的图片需要重新检测。";
      pushLog(batch.logs, "重新检测跳过：没有未修改的图片。");
      return;
    }
    if (!targetPaths.length) throw new Error("没有可检测的图片。");
    const targetKeys = new Set(targetItems.map(batchStableKey));
    const data = await api("/api/batch/detect", {
      input_dir: batch.inputDir,
      output_dir: batch.outputDir,
      images: targetPaths,
      options: batch.options,
      preserve_detection_state: isRedetect,
    });
    batch.jobId = data.job_id;
    batch.serverLogCount = 0;
    batch.jobMode = "detect";
    batch.detectMode = isRedetect ? "redetect" : "full";
    batch.redetectTargetKeys = Array.from(targetKeys);
    batch.progressTotal = targetPaths.length;
    if (!isRedetect) batch.detected = false;
    batch.processing = true;
    batch.done = 0;
    batch.pending = targetPaths.length;
    batch.status = `${isRedetect ? "重新检测中" : "检测中"} 0 / ${targetPaths.length}`;
    pushLog(batch.logs, `${isRedetect ? "开始重新检测未修改图片" : "开始批量检测"}：${targetPaths.length} 个文件。`);
    pollJob();
  } catch (error) {
    batch.processing = false;
    batch.progressTotal = 0;
    showError(error);
  }
}

async function exportBatch() {
  try {
    if (!batch.inputDir.trim()) throw new Error("输入目录为空。");
    if (!batch.outputDir.trim()) throw new Error("输出目录为空。");
    if (!batch.detected || !batch.items.length) throw new Error("请先完成批量检测。");
    const data = await api("/api/batch/export", {
      input_dir: batch.inputDir,
      output_dir: batch.outputDir,
      items: batch.items.map((item) => ({ ...item, options: item.options || batch.options })),
      options: batch.options,
    });
    batch.jobId = data.job_id;
    batch.serverLogCount = 0;
    batch.jobMode = "export";
    batch.progressTotal = batch.items.length;
    batch.processing = true;
    batch.status = "导出中 0 / " + batch.items.length;
    pushLog(batch.logs, `确认导出开始：${batch.items.length} 个检测结果。`);
    pollJob();
  } catch (error) {
    batch.processing = false;
    batch.progressTotal = 0;
    showError(error);
  }
}

async function pollJob() {
  if (!batch.jobId) return;
  try {
    const data = await api(`/api/jobs/${batch.jobId}`);
    const job = data.job;
    const isRedetect = job.kind === "detect" && batch.detectMode === "redetect";
    job.logs.slice(batch.serverLogCount).forEach((line) => pushLog(batch.logs, line));
    batch.serverLogCount = job.logs.length;
    if (isRedetect) {
      batch.done = job.status === "done" ? job.total : job.index;
      batch.pending = Math.max(0, job.total - batch.done);
      batch.saved = totalBatchBoxes();
      batch.status = job.status === "done" ? "重新检测完成" : `重新检测中 ${job.index} / ${job.total}`;
      if (job.status !== "done") {
        window.setTimeout(pollJob, 700);
        return;
      }
      const targetKeys = new Set(batch.redetectTargetKeys);
      const replacements = new Map();
      job.items.forEach((item) => {
        const key = batchStableKey(item);
        if (targetKeys.has(key)) replacements.set(key, item);
      });
      let updatedCount = 0;
      batch.items = batch.items.map((item) => {
        const replacement = replacements.get(batchStableKey(item));
        if (!replacement || item.edited) return item;
        updatedCount += 1;
        return { ...replacement, options: cloneOptions(batch.options), updated_at: Date.now() };
      });
      batch.processing = false;
      batch.detected = true;
      batch.done = batch.items.length;
      batch.pending = 0;
      batch.saved = totalBatchBoxes();
      batch.progressTotal = 0;
      batch.status = `重新检测完成：${updatedCount} 张未修改图片，当前检测框 ${batch.saved} 个`;
      return;
    }
    batch.items = job.items;
    batch.done = job.items.filter((item) => item.status === "已完成" || item.status === "失败").length;
    if (job.kind === "detect") {
      batch.done = job.status === "done" ? job.items.length : job.index;
    }
    batch.pending = Math.max(0, job.total - batch.done);
    batch.saved = job.saved;
    const runningLabel = job.kind === "export" ? "导出中" : "检测中";
    const doneLabel = job.kind === "export" ? "导出完成" : "检测完成";
    batch.status = job.status === "done" ? doneLabel : `${runningLabel} ${job.index} / ${job.total}`;
    if (job.status !== "done") {
      window.setTimeout(pollJob, 700);
      return;
    }
    batch.processing = false;
    if (job.kind === "detect") {
      batch.detected = true;
      batch.items = job.items.map((item) => ({ ...item, options: cloneOptions(batch.options), updated_at: Date.now() }));
      batch.done = batch.items.length;
      batch.pending = 0;
      batch.status = `检测完成：${batch.items.length} 张图片，检测框 ${job.saved} 个`;
      batch.progressTotal = 0;
      return;
    }
    batch.progressTotal = 0;
    showModal("success", "导出完成", `已输出 ${job.saved} 张照片。`, job.output_dir);
  } catch (error) {
    batch.processing = false;
    batch.progressTotal = 0;
    showError(error);
  }
}

function clearBatch() {
  batch.items = [];
  batch.done = 0;
  batch.pending = 0;
  batch.saved = 0;
  batch.detected = false;
  batch.jobMode = "";
  batch.detectMode = "full";
  batch.progressTotal = 0;
  batch.redetectTargetKeys = [];
  batch.status = "未扫描文件";
  batchReturnTargetId.value = "";
  batchReturnScrollTop.value = 0;
  batchReturnOffsetTop.value = 0;
  pushLog(batch.logs, "已清除选择。");
}

function clearSingle() {
  single.source = "";
  single.imageId = "";
  single.imageUrl = "";
  single.imageWidth = 1;
  single.imageHeight = 1;
  single.boxes = [];
  single.undo = [];
  single.detected = false;
  singleZoom.value = 1;
  selectedBox.value = null;
  single.fromBatch = false;
  single.batchImageId = "";
  batchReturnTargetId.value = "";
  batchReturnScrollTop.value = 0;
  batchReturnOffsetTop.value = 0;
  single.status = "请选择单张照片";
  pushLog(single.logs, "已清除选择。");
}

function openBatchItem(item, event) {
  if (!item?.image_id) return;
  const scroller = batchDetectionRef.value;
  batchReturnScrollTop.value = scroller?.scrollTop || 0;
  if (scroller && event?.currentTarget) {
    const scrollerRect = scroller.getBoundingClientRect();
    const targetRect = event.currentTarget.getBoundingClientRect();
    batchReturnOffsetTop.value = targetRect.top - scrollerRect.top;
  } else {
    batchReturnOffsetTop.value = 0;
  }
  batchReturnTargetId.value = batchItemKey(item);
  single.source = item.path || "";
  single.outputDir = batch.outputDir;
  single.imageId = item.image_id;
  single.imageUrl = cacheBustUrl(item.image_url, item.updated_at || Date.now());
  single.imageWidth = item.width || 1;
  single.imageHeight = item.height || 1;
  single.boxes = (item.boxes || []).map((box) => [...box]);
  single.undo = [];
  single.options = cloneOptions(item.options || batch.options);
  single.detected = true;
  single.processing = false;
  single.fromBatch = true;
  single.batchImageId = item.image_id;
  singleZoom.value = 1;
  selectedBox.value = single.boxes.length ? 0 : null;
  single.status = `正在修正批量检测结果：${item.name}`;
  pushLog(single.logs, `打开批量检测结果：${item.name}，检测框 ${single.boxes.length} 个。`);
  tab.value = "single";
  nextTick().then(measureImage);
}

async function returnToBatchPreview() {
  if (!single.fromBatch || !single.batchImageId) {
    tab.value = "batch";
    await restoreBatchPreviewPosition(batchReturnTargetId.value);
    return;
  }
  try {
    const data = await api("/api/batch/item", {
      image_id: single.imageId || single.batchImageId,
      boxes: single.boxes,
      options: single.options,
    });
    const targetId = single.batchImageId;
    const index = batch.items.findIndex((item) => batchItemKey(item) === targetId || item.image_id === targetId);
    let returnTargetId = batchReturnTargetId.value || targetId;
    if (index >= 0) {
      batch.items[index] = {
        ...batch.items[index],
        image_id: data.image_id,
        image_url: data.image_url,
        full_image_url: data.full_image_url,
        width: data.width,
        height: data.height,
        boxes: data.boxes,
        box_count: data.box_count,
        saved: data.box_count,
        options: cloneOptions(single.options),
        edited: true,
        status: "已修改",
        updated_at: Date.now(),
      };
      returnTargetId = batchItemKey(batch.items[index]);
    }
    batch.saved = batch.items.reduce((sum, item) => sum + Number(item.box_count ?? item.boxes?.length ?? 0), 0);
    batch.status = `已保存修改：${batch.items[index]?.name || "当前图片"}`;
    pushLog(batch.logs, `已保存单图修改：${batch.items[index]?.name || targetId}，检测框 ${data.box_count} 个。`);
    single.fromBatch = false;
    single.batchImageId = "";
    tab.value = "batch";
    batchReturnTargetId.value = returnTargetId;
    await restoreBatchPreviewPosition(returnTargetId);
  } catch (error) {
    showError(error);
  }
}

async function detectSingle() {
  try {
    if (!single.source.trim()) throw new Error("单张照片为空。");
    single.progressTitle = single.detected ? "正在重新检测" : "正在检测并生成预览";
    single.status = single.detected ? "正在重新检测..." : "正在检测...";
    pushLog(single.logs, single.detected ? "重新检测开始。" : "检测并预览开始。");
    single.processing = true;
    const data = await api("/api/single/detect", {
      source: single.source,
      options: single.options,
      preserve_detection_state: single.fromBatch,
      preserve_image_id: single.batchImageId,
    });
    single.imageId = data.image_id;
    single.imageUrl = cacheBustUrl(data.image_url);
    single.imageWidth = data.width;
    single.imageHeight = data.height;
    single.boxes = data.boxes;
    single.undo = [];
    single.detected = true;
    selectedBox.value = single.boxes.length ? 0 : null;
    single.status = `检测到 ${single.boxes.length} 个检测框`;
    pushLog(single.logs, `检测完成：${single.boxes.length} 个检测框。`);
    await nextTick();
    measureImage();
  } catch (error) {
    showError(error);
  } finally {
    single.processing = false;
  }
}

function measureImage() {
  const wrap = stageRef.value;
  if (!wrap || !single.imageWidth || !single.imageHeight) return;
  const availableWidth = Math.max(1, wrap.clientWidth - 36);
  const availableHeight = Math.max(1, wrap.clientHeight - 36);
  stage.width = availableWidth;
  stage.height = availableHeight;
  stage.fitScale = Math.max(0.001, Math.min(1, availableWidth / single.imageWidth, availableHeight / single.imageHeight));
}

async function zoomSinglePreview(event) {
  if (!single.imageUrl) return;
  const wrap = stageRef.value;
  if (!wrap) return;
  const oldScale = previewScale.value;
  const rect = wrap.getBoundingClientRect();
  const offsetX = event.clientX - rect.left;
  const offsetY = event.clientY - rect.top;
  const ratio = event.deltaY > 0 ? 0.9 : 1.12;
  singleZoom.value = Math.max(0.35, Math.min(4, singleZoom.value * ratio));
  await nextTick();
  measureImage();
  const scaleRatio = previewScale.value / oldScale;
  wrap.scrollLeft = (wrap.scrollLeft + offsetX) * scaleRatio - offsetX;
  wrap.scrollTop = (wrap.scrollTop + offsetY) * scaleRatio - offsetY;
}

function startPreviewPan(event) {
  if (!single.imageUrl) return;
  if (singleZoom.value <= 1.01) {
    selectedBox.value = null;
    return;
  }
  if (event.button !== undefined && event.button !== 0) return;
  if (event.target.closest?.(".box, .handle")) return;
  const wrap = stageRef.value;
  if (!wrap) return;
  selectedBox.value = null;
  const canPan = wrap.scrollWidth > wrap.clientWidth + 2 || wrap.scrollHeight > wrap.clientHeight + 2;
  if (!canPan) return;
  event.preventDefault();
  event.currentTarget.setPointerCapture?.(event.pointerId);
  previewPan.value = {
    startX: event.clientX,
    startY: event.clientY,
    scrollLeft: wrap.scrollLeft,
    scrollTop: wrap.scrollTop,
  };
  window.addEventListener("pointermove", onPreviewPan);
  window.addEventListener("pointerup", stopPreviewPan, { once: true });
  window.addEventListener("pointercancel", stopPreviewPan, { once: true });
}

function onPreviewPan(event) {
  const state = previewPan.value;
  const wrap = stageRef.value;
  if (!state || !wrap) return;
  wrap.scrollLeft = state.scrollLeft - (event.clientX - state.startX);
  wrap.scrollTop = state.scrollTop - (event.clientY - state.startY);
}

function stopPreviewPan() {
  previewPan.value = null;
  window.removeEventListener("pointermove", onPreviewPan);
  window.removeEventListener("pointercancel", stopPreviewPan);
}

function pushUndo() {
  single.undo.push(single.boxes.map((box) => [...box]));
  if (single.undo.length > 30) single.undo.shift();
}

function addBox() {
  if (!single.imageUrl) return;
  pushUndo();
  const w = single.imageWidth;
  const h = single.imageHeight;
  single.boxes.push([Math.round(w * 0.25), Math.round(h * 0.25), Math.round(w * 0.75), Math.round(h * 0.75)]);
  selectedBox.value = single.boxes.length - 1;
  pushLog(single.logs, "手动新增检测框。");
}

function deleteBox() {
  if (selectedBox.value == null) return;
  pushUndo();
  single.boxes.splice(selectedBox.value, 1);
  selectedBox.value = single.boxes.length ? Math.min(selectedBox.value, single.boxes.length - 1) : null;
  pushLog(single.logs, "删除选中检测框。");
}

function undoBox() {
  const prev = single.undo.pop();
  if (!prev) return;
  single.boxes = prev;
  pushLog(single.logs, "已撤销上一步检测框调整。");
}

function splitBox(direction) {
  if (selectedBox.value == null) return;
  const box = single.boxes[selectedBox.value];
  pushUndo();
  if (direction === "vertical") {
    const mid = Math.round((box[0] + box[2]) / 2);
    single.boxes.splice(selectedBox.value, 1, [box[0], box[1], mid, box[3]], [mid, box[1], box[2], box[3]]);
    pushLog(single.logs, "选中检测框已纵向二等分。");
  } else {
    const mid = Math.round((box[1] + box[3]) / 2);
    single.boxes.splice(selectedBox.value, 1, [box[0], box[1], box[2], mid], [box[0], mid, box[2], box[3]]);
    pushLog(single.logs, "选中检测框已横向二等分。");
  }
}

function startDrag(index, mode, event) {
  selectedBox.value = index;
  pushUndo();
  drag.value = { index, mode, startX: event.clientX, startY: event.clientY, startBox: [...single.boxes[index]] };
  window.addEventListener("pointermove", onDrag);
  window.addEventListener("pointerup", stopDrag, { once: true });
}

function onDrag(event) {
  if (!drag.value || !previewScale.value) return;
  const { index, mode, startX, startY, startBox } = drag.value;
  const dx = (event.clientX - startX) / previewScale.value;
  const dy = (event.clientY - startY) / previewScale.value;
  let [x1, y1, x2, y2] = startBox;
  if (mode === "move") {
    x1 += dx;
    x2 += dx;
    y1 += dy;
    y2 += dy;
  } else {
    if (mode.includes("w")) x1 += dx;
    if (mode.includes("e")) x2 += dx;
    if (mode.includes("n")) y1 += dy;
    if (mode.includes("s")) y2 += dy;
  }
  x1 = Math.max(0, Math.min(single.imageWidth - 10, x1));
  y1 = Math.max(0, Math.min(single.imageHeight - 10, y1));
  x2 = Math.max(x1 + 10, Math.min(single.imageWidth, x2));
  y2 = Math.max(y1 + 10, Math.min(single.imageHeight, y2));
  single.boxes[index] = [Math.round(x1), Math.round(y1), Math.round(x2), Math.round(y2)];
}

function stopDrag() {
  drag.value = null;
  window.removeEventListener("pointermove", onDrag);
}

async function exportSingle() {
  try {
    single.processing = true;
    single.progressTitle = "正在导出";
    single.status = "正在导出选中检测框...";
    pushLog(single.logs, "确认导出开始。");
    const data = await api("/api/single/export", {
      image_id: single.imageId,
      output_dir: single.outputDir,
      boxes: single.boxes,
      options: single.options,
    });
    single.status = "导出完成";
    pushLog(single.logs, `导出完成：${data.saved} 张照片。`);
    showModal("success", "导出完成", `已导出 ${data.saved} 张照片。`, data.output_dir || single.outputDir);
  } catch (error) {
    showError(error);
  } finally {
    single.processing = false;
  }
}

onMounted(async () => {
  applyPreset(batch.options);
  applyPreset(single.options);
  window.addEventListener("resize", measureImage);

  try {
    const cfg = await api("/api/config");
    Object.assign(config, cfg);
    batch.options.preset = cfg.default_preset;
    single.options.preset = cfg.default_preset;
    applyPreset(batch.options);
    applyPreset(single.options);
    pushLog(batch.logs, `JPEG 保存质量：${cfg.jpeg_quality}。`);
    pushLog(single.logs, `JPEG 保存质量：${cfg.jpeg_quality}。`);
  } catch (error) {
    pushLog(batch.logs, `配置读取失败：${error.message || error}`);
    pushLog(single.logs, `配置读取失败：${error.message || error}`);
  }

  runtimeDetectTimer = window.setTimeout(loadRuntimeInfo, 600);
});

onBeforeUnmount(() => {
  window.clearTimeout(runtimeDetectTimer);
  window.removeEventListener("resize", measureImage);
  window.removeEventListener("pointermove", onPreviewPan);
  window.removeEventListener("pointercancel", stopPreviewPan);
  window.removeEventListener("pointermove", onWindowResizeMove);
  window.removeEventListener("pointercancel", stopWindowResize);
});
</script>
