<template>
  <div ref="boxRef" class="log-box">
    <p v-for="(line, index) in logs" :key="index" v-html="renderLine(line)"></p>
  </div>
</template>

<script setup>
import { nextTick, ref, watch } from "vue";

const props = defineProps({
  logs: {
    type: Array,
    default: () => [],
  },
});

const boxRef = ref(null);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderLine(line) {
  if (line && typeof line === "object" && typeof line.html === "string") return line.html;
  return escapeHtml(line);
}

watch(
  () => props.logs.length,
  async () => {
    await nextTick();
    if (boxRef.value) boxRef.value.scrollTop = boxRef.value.scrollHeight;
  },
);
</script>
