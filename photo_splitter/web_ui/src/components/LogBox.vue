<template>
  <div ref="boxRef" class="log-box">
    <p v-for="(line, index) in logs" :key="index">{{ line }}</p>
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

watch(
  () => props.logs.length,
  async () => {
    await nextTick();
    if (boxRef.value) boxRef.value.scrollTop = boxRef.value.scrollHeight;
  },
);
</script>
