<template>
  <section class="preview-pane">
    <header class="preview-heading">
      <strong>{{ title }}</strong>
      <span>{{ subtitle }}</span>
    </header>
    <div class="preview-body">
      <slot />
    </div>
  </section>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
  subtitle: {
    type: String,
    default: "",
  },
});
</script>
