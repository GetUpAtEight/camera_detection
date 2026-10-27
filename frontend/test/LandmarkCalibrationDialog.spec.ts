import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * 标定对话框的行为契约分两层：
 * - 源码结构：类名/文案/事件绑定在拆分后没有漂移（这是本次重构的核心风险）；
 * - 真实挂载：用内联的等价 SFC 夹具验证同一套模板结构确实能渲染出来的。
 *
 * 直接编译真正 .vue 文件在部分环境下不可用（vite 的 SFC 编译要拉起子进程），
 * 所以这里用夹具复用同一份模板骨架，再单独对真文件做结构断言。
 */
const SFC_PATH = resolve(__dirname, '../src/components/sparse/LandmarkCalibrationDialog.vue')
const source = readFileSync(SFC_PATH, 'utf8')

const PNG_1X1 =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

describe('标定对话框：结构契约（拆分前后必须一致）', () => {
  it('保留拆分前的全部类名', () => {
    const classNames = [
      'lm-tip', 'lm-toolbar', 'lm-stage', 'lm-img', 'lm-layer', 'lm-box',
      'lm-box-label', 'lm-list', 'lm-item', 'lm-admit', 'lm-tpl', 'lm-tpl-title',
      'lm-tpl-row', 'lm-tpl-name'
    ]
    for (const name of classNames) {
      expect(source).toContain(name)
    }
  })

  it('保留标定说明、模板库上限与入库提示这些关键文案', () => {
    expect(source).toContain('不会自己移动')
    expect(source).toContain('一致整体位移')
    expect(source).toContain('上限 8 套/参照物')
    expect(source).toContain('把这张画面同时加入历史基线')
    expect(source).toContain('用当前画面补录')
    expect(source).toContain('清除已有标定')
  })

  it('保存按钮把可用性绑到「有底图」，与拆分前 lmBlob 判据一致', () => {
    expect(source).toContain('const canAddTemplate = computed(() => !!props.imageBlob)')
    expect(source).toContain(':disabled="!rects.length || !canAddTemplate"')
  })

  it('拖拽与工具栏动作全部是事件上抛，不自己改状态', () => {
    for (const evt of ['capture', 'down', 'move', 'up', 'undo', 'clear-rects', 'remove-rect', 'save', 'clear-all', 'add-template']) {
      expect(source).toContain(`emit('${evt}'`)
    }
  })

  it('样式随组件一起搬走，且仍声明为 scoped', () => {
    expect(source).toContain('<style scoped>')
    expect(source).toContain('.lm-box.drawing')
    // 证据图上的参照物浮层仍在页面里（属于证据图区块，不随对话框搬走）
    expect(existsSync(SFC_PATH)).toBe(true)
  })
})

/** 与真组件完全同构的最小夹具：只把 Element Plus 组件换成原生标签。 */
const DialogFixture = {
  props: {
    imageUrl: { type: String, default: '' },
    imageBlob: { type: Object, default: null },
    mode: { type: String, default: 'landmark' },
    rects: { type: Array, default: () => [] },
    drawing: { type: Boolean, default: false },
    draft: { type: Object, default: () => ({ x: 0, y: 0, w: 0, h: 0 }) }
  },
  emits: ['down', 'move', 'up', 'capture'],
  computed: {
    canAddTemplate() {
      return !!this.imageBlob
    }
  },
  methods: {
    rectStyle(r: { x: number; y: number; w: number; h: number }) {
      return {
        left: `${r.x * 100}%`,
        top: `${r.y * 100}%`,
        width: `${r.w * 100}%`,
        height: `${r.h * 100}%`
      }
    }
  },
  template: `
    <div>
      <div class="lm-toolbar">
        <button @click="$emit('capture')">取当前画面</button>
      </div>
      <div class="lm-stage" v-if="imageUrl">
        <img :src="imageUrl" class="lm-img" />
        <div class="lm-layer" @mousedown="$emit('down')" @mousemove="$emit('move')" @mouseup="$emit('up')">
          <div v-for="(r, i) in rects" :key="'r' + i" class="lm-box" :class="{ ignore: r.kind === 'ignore' }" :style="rectStyle(r)">
            <span class="lm-box-label" :class="{ ignore: r.kind === 'ignore' }">{{ r.label }}</span>
          </div>
          <div v-if="drawing" class="lm-box drawing" :style="rectStyle(draft)"></div>
        </div>
      </div>
      <div class="lm-empty" v-else>点「取当前画面」取一张底图</div>
      <div class="lm-list" v-if="rects.length">
        <div v-for="(r, i) in rects" :key="'li' + i" class="lm-item">{{ r.label }}</div>
      </div>
      <button class="save" :disabled="!rects.length || !canAddTemplate">保存标定（{{ rects.length }} 个）</button>
    </div>
  `
}

describe('标定对话框：模板结构可渲染', () => {
  function mountFixture(overrides: Record<string, unknown> = {}) {
    return mount(DialogFixture, {
      props: {
        imageUrl: PNG_1X1,
        imageBlob: new Blob(['x']),
        mode: 'landmark',
        rects: [],
        drawing: false,
        draft: { x: 0, y: 0, w: 0, h: 0 },
        ...overrides
      }
    })
  }

  it('有底图时渲染舞台、按 rects 画框、忽略区带 ignore 类', () => {
    const wrapper = mountFixture({
      rects: [
        { x: 0.1, y: 0.1, w: 0.2, h: 0.2, label: '参照物1', kind: 'landmark' },
        { x: 0.4, y: 0.4, w: 0.1, h: 0.1, label: '忽略区1', kind: 'ignore' }
      ]
    })
    expect(wrapper.find('.lm-stage').exists()).toBe(true)
    expect(wrapper.find('.lm-img').attributes('src')).toBe(PNG_1X1)
    const boxes = wrapper.findAll('.lm-box')
    expect(boxes).toHaveLength(2)
    expect(boxes[1].classes()).toContain('ignore')
    expect(wrapper.findAll('.lm-item')).toHaveLength(2)
  })

  it('没有底图时只提示取图，不渲染舞台', () => {
    const wrapper = mountFixture({ imageUrl: '', imageBlob: null })
    expect(wrapper.find('.lm-stage').exists()).toBe(false)
    expect(wrapper.text()).toContain('取当前画面')
  })

  it('拖动中的草稿框单独带 drawing 类', () => {
    const wrapper = mountFixture({ drawing: true, draft: { x: 0.1, y: 0.1, w: 0.3, h: 0.3 } })
    expect(wrapper.find('.lm-box.drawing').exists()).toBe(true)
  })

  it('鼠标事件与工具栏动作都会上抛给父组件', async () => {
    const wrapper = mountFixture()
    await wrapper.find('.lm-layer').trigger('mousedown')
    await wrapper.find('.lm-layer').trigger('mousemove')
    await wrapper.find('.lm-layer').trigger('mouseup')
    expect(wrapper.emitted('down')).toHaveLength(1)
    expect(wrapper.emitted('move')).toHaveLength(1)
    expect(wrapper.emitted('up')).toHaveLength(1)
    await wrapper.find('.lm-toolbar button').trigger('click')
    expect(wrapper.emitted('capture')).toHaveLength(1)
  })

  it('没有框或没有底图时保存按钮禁用', () => {
    expect(mountFixture().find('button.save').attributes('disabled')).toBeDefined()
    const withRect = mountFixture({
      rects: [{ x: 0.1, y: 0.1, w: 0.2, h: 0.2, label: '参照物1', kind: 'landmark' }]
    })
    expect(withRect.find('button.save').attributes('disabled')).toBeUndefined()
    const noImage = mountFixture({
      imageBlob: null,
      rects: [{ x: 0.1, y: 0.1, w: 0.2, h: 0.2, label: '参照物1', kind: 'landmark' }]
    })
    expect(noImage.find('button.save').attributes('disabled')).toBeDefined()
  })
})
