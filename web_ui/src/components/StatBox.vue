<template>
  <article :class="tone">
    <span>{{ label }}</span>
    <strong>{{ value }}</strong>
  </article>
</template>

<script setup>
defineProps({
  label: {
    type: String,
    required: true,
  },
  value: {
    type: [Number, String],
    default: 0,
  },
  tone: {
    type: String,
    default: "default",
  },
});
</script>
