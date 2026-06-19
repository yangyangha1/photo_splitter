<template>
  <h2 class="panel-title">{{ title }}</h2>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
});
</script>
