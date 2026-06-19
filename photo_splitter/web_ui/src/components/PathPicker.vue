<template>
  <label class="path-picker">
    <span>{{ label }}</span>
    <div>
      <input
        :value="modelValue"
        @input="$emit('update:modelValue', $event.target.value)"
        @change="$emit('commit', $event.target.value)"
      />
      <button @click.prevent="$emit('pick')">{{ buttonLabel || "..." }}</button>
    </div>
  </label>
</template>

<script setup>
defineProps({
  label: {
    type: String,
    required: true,
  },
  modelValue: {
    type: String,
    default: "",
  },
  buttonLabel: {
    type: String,
    default: "...",
  },
});

defineEmits(["update:modelValue", "pick", "commit"]);
</script>
