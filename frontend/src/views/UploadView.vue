<template>
  <div>
    <div class="ctd-page-head">
      <h2>媒体上传</h2>
      <p>上传图片或视频，作为摄像头篡改检测的输入。视频会自动抽帧后逐帧检测。</p>
    </div>

    <el-alert
      v-if="!store.algoOnline"
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom: 16px"
      title="算法服务当前不可用"
      :description="store.algoError || '请先启动 Python 算法层（algo/server.py，默认 8000 端口），否则上传后无法检测。'"
    />

    <div class="upload-layout">
      <div class="ctd-card upload-card">
        <h3 class="ctd-card-title">
          <el-icon><UploadFilled /></el-icon> 选择文件
          <span class="sub">图片 jpg/png/webp，视频 mp4/avi/mov/mkv/webm，单文件 ≤ 512MB</span>
        </h3>

        <el-upload
          v-if="!pending"
          drag
          :auto-upload="false"
          :show-file-list="false"
          :on-change="onPicked"
          accept="image/*,video/*"
          class="drop-zone"
        >
          <div class="drop-inner">
            <el-icon class="drop-icon"><FolderOpened /></el-icon>
            <div class="drop-title">把文件拖到这里，或 <em>点击选择</em></div>
            <div class="drop-hint">支持单张图片 / 单段视频；视频建议 1080p 以内、30 秒内以获得更快演示速度</div>
          </div>
        </el-upload>

        <div v-else class="pending">
          <div class="pending-row">
            <el-icon class="file-icon"><Document /></el-icon>
            <div class="file-meta">
              <div class="file-name">{{ pending.name }}</div>
              <div class="ctd-muted">{{ formatBytes(pending.size) }} · {{ kindText }}</div>
            </div>
            <el-button text size="small" @click="resetPending">重新选择</el-button>
          </div>

          <div v-if="isImage" class="reference-box">
            <div class="reference-head">
              <span>参考帧（可选）</span>
              <span class="ctd-muted">帧差 / ORB / 边缘消失率需要参考帧才能工作，不提供时这 3 个算法会被跳过</span>
            </div>
            <el-upload
              :auto-upload="false"
              :show-file-list="false"
              :on-change="onReferencePicked"
              accept="image/*"
            >
              <el-button size="small" :icon="Picture">选择参考图片</el-button>
            </el-upload>
            <span v-if="reference" class="reference-name">
              {{ reference.name }}
              <el-button text size="small" @click="reference = null">移除</el-button>
            </span>
          </div>

          <div class="pending-actions">
            <el-progress
              v-if="uploading"
              :percentage="progress"
              :stroke-width="8"
              style="flex: 1"
              :status="progress === 100 ? 'success' : undefined"
            />
            <template v-else>
              <el-button type="primary" :icon="Upload" @click="doUpload">开始上传</el-button>
              <el-button @click="resetPending">取消</el-button>
            </template>
          </div>
        </div>

        <div v-if="uploadedItem" class="upload-success">
          <el-icon><CircleCheckFilled /></el-icon>
          <span>「{{ uploadedItem.originalName }}」上传成功</span>
          <el-button size="small" type="primary" text @click="goDetect(uploadedItem.id)">立即运行检测 →</el-button>
        </div>
      </div>

      <div class="ctd-card tips-card">
        <h3 class="ctd-card-title"><el-icon><InfoFilled /></el-icon> 演示建议</h3>
        <ol class="tips">
          <li>
            <b>样本准备</b><br />
            同一机位下，先拍一段正常画面作为基线，再拍摄被遮挡 / 失焦 / 转动的片段。
          </li>
          <li>
            <b>参考帧的作用</b><br />
            视频自动取首帧为参考帧；单张图片可手动上传一张正常画面作为参考帧。
          </li>
          <li>
            <b>算法组合</b><br />
            遮挡看「直方图熵 + 帧差」，失焦看「拉普拉斯方差」，移位看「ORB 特征匹配」。
          </li>
          <li>
            <b>结果解读</b><br />
            异常分 0.5 即为判定阈值；多算法同时告警才会被认定为篡改事件。
          </li>
        </ol>
      </div>
    </div>

    <div class="ctd-card">
      <h3 class="ctd-card-title">
        <el-icon><Files /></el-icon> 媒体库
        <span class="sub">共 {{ store.mediaList.length }} 个文件</span>
        <el-button text size="small" style="margin-left: auto" :icon="Refresh" @click="store.loadMedia()">刷新</el-button>
      </h3>

      <el-table :data="store.mediaList" size="default" stripe empty-text="还没有上传任何媒体文件">
        <el-table-column label="文件名" min-width="240">
          <template #default="{ row }">
            <div class="name-cell">
              <el-tag size="small" :type="row.mediaType === 'VIDEO' ? 'warning' : 'success'" effect="plain">
                {{ row.mediaType === 'VIDEO' ? '视频' : '图片' }}
              </el-tag>
              <span class="file-name" :title="row.originalName">{{ row.originalName }}</span>
              <el-tag v-if="row.hasReference" size="small" type="info" effect="plain">含参考帧</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="分辨率" width="120">
          <template #default="{ row }">
            <span class="ctd-mono">{{ row.width ? `${row.width}×${row.height}` : '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="视频信息" width="180">
          <template #default="{ row }">
            <span v-if="row.mediaType === 'VIDEO'" class="ctd-mono">
              {{ formatDuration(row.durationMs) }} · {{ row.frameCount || '-' }} 帧
            </span>
            <span v-else class="ctd-muted">静态图片</span>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ formatBytes(row.fileSize) }}</template>
        </el-table-column>
        <el-table-column label="上传时间" width="170">
          <template #default="{ row }">
            <span class="ctd-mono">{{ formatTime(row.createdAt) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button text size="small" @click="preview(row)">预览</el-button>
            <el-button text size="small" type="primary" @click="goDetect(row.id)">去检测</el-button>
            <el-popconfirm title="删除后关联的检测任务也会一并删除，确定？" width="260" @confirm="remove(row)">
              <template #reference>
                <el-button text size="small" type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="previewVisible" :title="previewItem?.originalName" width="820px" destroy-on-close>
      <div class="preview-body">
        <video
          v-if="previewItem?.mediaType === 'VIDEO'"
          :src="mediaApi.fileUrl(previewItem.id)"
          controls
          autoplay
          class="preview-media"
        ></video>
        <img v-else :src="mediaApi.fileUrl(previewItem!.id)" class="preview-media" alt="preview" />
      </div>
      <template #footer>
        <el-button @click="previewVisible = false">关闭</el-button>
        <el-button type="primary" @click="goDetect(previewItem!.id)">运行检测</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { CircleCheckFilled, Document, Files, FolderOpened, InfoFilled, Picture, Refresh, Upload, UploadFilled } from '@element-plus/icons-vue'
import type { MediaItem } from '@/types'
import { mediaApi } from '@/api'
import { useAppStore } from '@/store/app'
import { formatBytes, formatDuration, formatTime } from '@/utils/format'

const store = useAppStore()
const router = useRouter()

const pending = ref<File | null>(null)
const reference = ref<File | null>(null)
const uploading = ref(false)
const progress = ref(0)
const uploadedItem = ref<MediaItem | null>(null)
const previewVisible = ref(false)
const previewItem = ref<MediaItem | null>(null)

const IMAGE_EXT = ['jpg', 'jpeg', 'png', 'bmp', 'webp', 'tif', 'tiff']

const isImage = computed(() => {
  if (!pending.value) return false
  const ext = pending.value.name.split('.').pop()?.toLowerCase() || ''
  return pending.value.type.startsWith('image/') || IMAGE_EXT.includes(ext)
})

const kindText = computed(() => (isImage.value ? '图片' : '视频'))

function onPicked(uploadFile: any) {
  const file = uploadFile.raw as File
  if (!file) return
  const ext = file.name.split('.').pop()?.toLowerCase() || ''
  const isVideo = file.type.startsWith('video/') || ['mp4', 'avi', 'mov', 'mkv', 'webm', 'm4v'].includes(ext)
  const isImageFile = file.type.startsWith('image/') || IMAGE_EXT.includes(ext)
  if (!isVideo && !isImageFile) {
    ElMessage.error('只支持图片或视频文件')
    return
  }
  pending.value = file
  reference.value = null
  uploadedItem.value = null
  progress.value = 0
}

function onReferencePicked(uploadFile: any) {
  const file = uploadFile.raw as File
  if (!file) return
  if (!file.type.startsWith('image/')) {
    ElMessage.error('参考帧必须是图片')
    return
  }
  reference.value = file
}

function resetPending() {
  pending.value = null
  reference.value = null
  progress.value = 0
  uploading.value = false
}

async function doUpload() {
  if (!pending.value) return
  uploading.value = true
  progress.value = 0
  try {
    const item = await mediaApi.upload(pending.value, reference.value, (event) => {
      if (event.total) {
        progress.value = Math.min(99, Math.round((event.loaded / event.total) * 100))
      }
    })
    progress.value = 100
    uploadedItem.value = item
    await store.loadMedia()
    store.setMedia(item.id)
    ElMessage.success('上传成功')
    setTimeout(() => {
      pending.value = null
      uploading.value = false
      progress.value = 0
    }, 500)
  } catch (e: any) {
    uploading.value = false
    progress.value = 0
    ElMessage.error(e?.message || '上传失败')
  }
}

function preview(item: MediaItem) {
  previewItem.value = item
  previewVisible.value = true
}

function goDetect(id: string) {
  store.setMedia(id)
  previewVisible.value = false
  router.push({ name: 'detect', query: { mediaId: id } })
}

async function remove(item: MediaItem) {
  await mediaApi.remove(item.id)
  ElMessage.success('已删除')
  await store.loadMedia()
}
</script>

<style scoped>
.upload-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.upload-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.drop-zone :deep(.el-upload-dragger) {
  padding: 34px 20px;
  border-radius: 12px;
  border: 1.5px dashed #cfd8e6;
  background: linear-gradient(180deg, #fbfcff, #f6f9ff);
  transition: all 0.2s ease;
}

.drop-zone :deep(.el-upload-dragger:hover) {
  border-color: var(--ctd-primary);
  background: #f2f7ff;
}

.drop-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.drop-icon {
  font-size: 42px;
  color: var(--ctd-primary);
  opacity: 0.85;
}

.drop-title {
  font-size: 14px;
  color: var(--ctd-text);
}

.drop-title em {
  color: var(--ctd-primary);
  font-style: normal;
  font-weight: 600;
}

.drop-hint {
  font-size: 12px;
  color: #98a1b0;
}

.pending {
  border: 1px solid var(--ctd-border);
  border-radius: 12px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pending-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.file-icon {
  font-size: 30px;
  color: var(--ctd-primary);
}

.file-meta {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reference-box {
  border-top: 1px dashed var(--ctd-border);
  padding-top: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.reference-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  flex: 1;
  min-width: 220px;
}

.reference-name {
  font-size: 12px;
  color: var(--ctd-primary);
}

.pending-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.upload-success {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--ctd-success);
  background: #f3fbf7;
  border: 1px solid #d8f0e4;
  border-radius: 10px;
  padding: 10px 12px;
}

.tips-card .tips {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.85;
  color: var(--ctd-text-secondary);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tips-card .tips b {
  color: var(--ctd-text);
  font-weight: 600;
}

.name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.name-cell .file-name {
  max-width: 260px;
}

.preview-body {
  background: #0d1117;
  border-radius: 10px;
  display: flex;
  justify-content: center;
  min-height: 280px;
}

.preview-media {
  max-width: 100%;
  max-height: 62vh;
}

@media (max-width: 1100px) {
  .upload-layout {
    grid-template-columns: 1fr;
  }
}
</style>
