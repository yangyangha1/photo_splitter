<template>
  <section class="parameter-section">
    <h2 class="param-title">参数设置</h2>
    <div class="parameter-panel">
      <div class="param-head">
        <button @click="resetPreset">恢复默认</button>
      </div>

      <div class="field">
        <label>处理配置预设</label>
        <div class="ios-segment preset">
          <button
            v-for="preset in config.presets"
            :key="preset.key"
            :class="{ active: options.preset === preset.key }"
            @click="selectPreset(preset.key)"
          >
            {{ preset.name }}
          </button>
        </div>
      </div>

      <div class="field">
        <label>源图底色</label>
        <div class="ios-segment">
          <button
            v-for="item in config.background_modes"
            :key="item.key"
            :class="{ active: options.background_mode === item.key }"
            @click="setBackgroundMode(item)"
          >
            {{ item.label }}
          </button>
        </div>
      </div>

      <div class="field">
        <label>人脸判断自动旋转</label>
        <div class="ios-segment">
          <button :class="{ active: options.auto_face_rotate === false }" @click="setFaceRotate(false)">
            关闭
          </button>
          <button :class="{ active: options.auto_face_rotate === true }" @click="setFaceRotate(true)">
            开启
          </button>
        </div>
      </div>

      <div class="field range">
        <label>检测边界阈值 <b>{{ options.dark_threshold }}</b></label>
        <input v-model.number="options.dark_threshold" type="range" min="0" max="255" @change="emitChange('检测边界阈值', options.dark_threshold)" />
      </div>

      <div class="field range">
        <label>最小照片面积 <b>{{ minAreaLabel }}</b></label>
        <input
          v-model.number="options.min_area_ratio"
          type="range"
          min="0.0008"
          max="0.006"
          step="0.0001"
          @change="emitChange('最小照片面积', minAreaLabel)"
        />
      </div>

      <div class="field range">
        <label>裁切白边阈值 <b>{{ options.white_threshold }}</b></label>
        <input v-model.number="options.white_threshold" type="range" min="180" max="250" @change="emitChange('裁切白边阈值', options.white_threshold)" />
      </div>

      <div class="field range">
        <label>倾斜矫正敏感度 <b>{{ options.skew_gain_percent }}%</b></label>
        <input v-model.number="options.skew_gain_percent" type="range" min="2" max="12" @change="emitChange('倾斜矫正敏感度', `${options.skew_gain_percent}%`)" />
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  config: {
    type: Object,
    required: true,
  },
  options: {
    type: Object,
    required: true,
  },
});

const emit = defineEmits(["apply-preset", "parameter-change"]);

const minAreaLabel = computed(() => `${(Number(props.options.min_area_ratio || 0) * 100).toFixed(2)}%`);

function selectPreset(key) {
  const preset = props.config.presets.find((item) => item.key === key);
  props.options.preset = key;
  emit("apply-preset");
  emitChange("处理配置预设", preset?.name || key);
}

function resetPreset() {
  props.options.preset = props.config.default_preset;
  emit("apply-preset");
  const preset = props.config.presets.find((item) => item.key === props.config.default_preset);
  emitChange("恢复默认", preset?.name || props.config.default_preset);
}

function setBackgroundMode(item) {
  props.options.background_mode = item.key;
  emitChange("源图底色", item.label);
}

function setFaceRotate(value) {
  props.options.auto_face_rotate = value;
  emitChange("人脸判断自动旋转", value ? "开启" : "关闭");
}

function emitChange(name, value) {
  emit("parameter-change", { name, value });
}
</script>
