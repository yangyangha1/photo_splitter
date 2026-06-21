<template>
  <div class="intro">
    <h1>{{ content.title }}</h1>
    <p v-html="htmlText(content.summary)"></p>

    <div class="intro-cards">
      <article v-for="card in content.cards" :key="card.title">
        <b>{{ card.title }}</b>
        <span v-html="htmlText(card.body)"></span>
      </article>
    </div>

    <article class="intro-full">
      <b>{{ content.detailTitle }}</b>
      <p v-for="line in content.details" :key="line" v-html="htmlText(line)"></p>
    </article>

    <p class="intro-credit">
      设计开发：YY<br />
      代码完成：CODEX
    </p>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  mode: {
    type: String,
    required: true,
  },
});

const batchContent = {
  title: "照片拼图批量分割工具",
  summary: "<b>批量扫描相册、拼版照片和同页多图照片，自动分割并按源分辨率保存照片。</b>",
  cards: [
    {
      title: "批量检测",
      body: "批量检测分割区域并显示预览，点击预览图进入单独处理界面。全部确认后按源分辨率保存照片。",
    },
    {
      title: "批量输出规则",
      body: "输入目录的根目录散图输出到新建单独文件夹内，输入目录的子目录内的图片全部保存在同级文件夹内。",
    },
    {
      title: "操作说明",
      body: "1.选择输入输出目录；\/br 2.调整检测参数；\/br 3.批量检测并微调检测结果；\/br 4.确认导出照片",
    },
  ],
  detailTitle: "详细参数说明",
  details: [
    "<b>处理配置预设</b>：针对不同场景的默认预设配置。",
    "<b>源图底色</b>：选择正确的背景色可以显著提高识别精准度。",
    "<b>检测边界阈值</b>：控制暗边、相框和分割线参与检测的强度，数值越高越保守。",
    "<b>最小照片面积</b>：用于过滤小噪点和误检碎片，若一张大图里不同照片尺寸差异明显，可以适当降低。",
    "<b>裁切白边阈值</b>：控制输出照片裁白边的积极程度，可能会裁切照片的白色天空，数值越高越保守。",
    "<b>人脸判断自动旋转</b>：使用OpenCV FaceDetectorYN算法辅助判断照片方向，需要旋转的在图片检测框上标记箭头，以箭头向上的方向保存照片。",
	"<b>保存预览分割图</b>：输出时是否保存带有分割标线的预览图。",
  ],
};

const singleContent = {
  title: "单张照片精修分割",
  summary: "  <b>自动检测拼版照片和同页多图照片的分割区域，手动调整区域后按源分辨率保存照片。</b>",
  cards: [
    {
      title: "单图检测预览",
      body: "选择照片后显示缩略图，点击检测并预览，显示检测框，可直接在图上修改范围。使用滚轮放大缩小预览图。",
    },
    {
      title: "单图编辑规则",
      body: "检测框支持拖动、缩放、纵向二等分、横向二等分、新增、删除和撤销，便于修正误判。",
    },
    {
      title: "操作说明",
      body: "1.选择照片和输出目录；\/br 2.调整检测参数；\/br 3.点击检测并预览；\/br 4.调整分割框后确认导出。",
    },
  ],
  detailTitle: "详细参数说明",
  details: [
    "<b>处理配置预设</b>：针对不同场景的默认预设配置。",
    "<b>源图底色</b>：选择正确的背景色可以显著提高识别精准度。",
    "<b>检测边界阈值</b>：控制暗边、相框和分割线参与检测的强度，数值越高越保守。",
    "<b>最小照片面积</b>：用于过滤小噪点和误检碎片，若一张大图里不同照片尺寸差异明显，可以适当降低。",
    "<b>裁切白边阈值</b>：控制输出照片裁白边的积极程度，可能会裁切照片的白色天空，数值越高越保守。",
    "<b>人脸判断自动旋转</b>：使用OpenCV FaceDetectorYN算法辅助判断照片方向，需要旋转的在图片检测框上标记箭头，可以手动调整方向。手动添加的检测框需要手动旋转。",
	"<b>保存预览分割图</b>：输出时是否保存带有分割标线的预览图。",
  ],
};

const content = computed(() => (props.mode === "batch" ? batchContent : singleContent));

function htmlText(value) {
  return String(value ?? "").replaceAll("/br", "<br />");
}
</script>
