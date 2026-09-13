<script setup lang="ts">
// 区域画布 spike：验证 Vue Flow 的 sub-flow 分组能否承载 DocMind 的"分区 + 文件 + 跨区契约"模型。
// mock 数据贴 StarVoyager：values(数值) / behaviors(角色行为) / ui 三区，文件卡为子节点，
// 边表示"依赖被依赖区的导出符号"这一跨区契约。
import { ref } from 'vue'
import {
  VueFlow,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
  MarkerType,
} from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import type { Node, Edge, NodeChange, EdgeChange, Connection, NodeMouseHandler } from '@vue-flow/core'
import RegionNode from './RegionNode.vue'
import type { RegionData } from './RegionNode.vue'
import FileCardNode from './FileCardNode.vue'
import type { FileCardData } from './FileCardNode.vue'

const nodeTypes = { region: RegionNode, file: FileCardNode }

const C = { values: '#58a6ff', behaviors: '#bc8cff', ui: '#f072b6' }

// 区域固定布局（group 容器）；子节点坐标相对父区域
const REGION_W: Record<string, number> = { values: 340, behaviors: 360, ui: 340 }
const CARD_Y = [56, 120, 184]

function region(id: string, key: string, name: string, pos: { x: number; y: number },
  height: number, desc: string, count: number): Node<RegionData> {
  return {
    id,
    type: 'region',
    position: pos,
    // v1：尺寸用节点顶层 width/height（会被应用为 wrapper inline style），
    // 放 style 里会被 NodeResizer 挂载时按 min-width/min-height 重置
    width: REGION_W[key],
    height,
    data: { name, key, color: C[key as keyof typeof C], count, desc },
  }
}

function file(id: string, parent: string, idx: number, name: string, chip: string,
  color: string, symbols: string, dirty = false): Node<FileCardData> {
  return {
    id,
    type: 'file',
    // 注意：Vue Flow v1 的父节点字段是 parentNode（与 React Flow 的 parentId 不同，
    // 类型定义注明下个 major 才改名 parentId）
    parentNode: parent,
    extent: 'parent', // 关键约束：文件卡不允许被拖出所属区域
    position: { x: 16, y: CARD_Y[idx] },
    data: { name, chip, color, symbols, dirty },
  }
}

const initialNodes: Node[] = [
  // 左列：values（被依赖）在下、ui 在上？—— values 放 (40,60)，ui 放 (40,430) 形成垂直契约
  region('region-values', 'values', '数值区', { x: 40, y: 60 }, 284,
    '纯数据与平衡表，被 behaviors/ui 依赖，不得反向依赖', 3),
  file('file-stats', 'region-values', 0, 'player_stats.gd', 'GD', C.values, 'max_hp · move_speed · fire_rate'),
  file('file-enemycfg', 'region-values', 1, 'enemy_config.gd', 'GD', C.values, 'ENEMY_HP · CONTACT_DMG', true),
  file('file-balance', 'region-values', 2, 'balance.tres', 'TRES', C.values, '全局数值表资源'),

  region('region-behaviors', 'behaviors', '角色行为区', { x: 470, y: 0 }, 284,
    '玩家/敌人/生成器逻辑，仅可依赖 values 的导出', 3),
  file('file-player', 'region-behaviors', 0, 'player.gd', 'GD', C.behaviors, 'class Player · take_damage()', true),
  file('file-enemy', 'region-behaviors', 1, 'enemy.gd', 'GD', C.behaviors, 'class Enemy · _on_body_entered'),
  file('file-spawner', 'region-behaviors', 2, 'spawner.gd', 'GD', C.behaviors, 'spawn_wave()'),

  region('region-ui', 'ui', 'UI · HUD 区', { x: 40, y: 430 }, 220,
    '界面只读消费数值与事件，不写玩法逻辑', 2),
  file('file-hud', 'region-ui', 0, 'hud.gd', 'GD', C.ui, 'hp_bar · event_log'),
  file('file-menu', 'region-ui', 1, 'main_menu.gd', 'GD', C.ui, 'Start / Quit'),
]

function contractEdge(id: string, source: string, target: string): Edge {
  return {
    id,
    source,
    sourceHandle: 'out',
    target,
    targetHandle: 'in',
    type: 'smoothstep',
    animated: true,
    class: 'edge-contract',
    style: { stroke: C.values, strokeWidth: 1.6 },
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: C.values },
  }
}

const initialEdges: Edge[] = [
  // behaviors 依赖 values 的导出（水平跨区）
  contractEdge('contract-player-stats', 'file-player', 'file-stats'),
  contractEdge('contract-enemy-cfg', 'file-enemy', 'file-enemycfg'),
  // ui 只读消费 values（垂直跨区）
  contractEdge('contract-hud-stats', 'file-stats', 'file-hud'),
]

// ---- 受控状态（v1 标准纯函数写法，节点/边变化先过 apply* 再回流）
const nodes = ref<Node[]>(initialNodes)
const edges = ref<Edge[]>(initialEdges)
const selectedId = ref<string | null>(null)

function onNodesChange(changes: NodeChange[]) {
  nodes.value = applyNodeChanges(changes, nodes.value)
}
function onEdgesChange(changes: EdgeChange[]) {
  edges.value = applyEdgeChanges(changes, edges.value)
}
function onConnect(conn: Connection) {
  // 手动新建边沿用与契约边一致的 smoothstep/虚线/箭头样式（绿色区分用户手画）
  edges.value = addEdge({
    ...conn,
    type: 'smoothstep',
    animated: true,
    class: 'edge-contract',
    style: { stroke: '#3fb950', strokeWidth: 1.6 },
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: '#3fb950' },
  }, edges.value)
}
const onNodeClick: NodeMouseHandler = (_e, n) => { selectedId.value = n.id }

function miniColor(n: Node): string {
  return (n.data as { color?: string })?.color ?? '#3a424d'
}

function reset() {
  // 深拷贝初始数据，避免残留拖拽位置
  nodes.value = JSON.parse(JSON.stringify(initialNodes))
  edges.value = JSON.parse(JSON.stringify(initialEdges))
  selectedId.value = null
}

// 暴露给浏览器自动化探针：paneReady 回传完整 VueFlowStore（getNodes 在 store 上是数组，
// findNode 才是函数），这里包一层窄门面避免探针依赖 store 形状
function onPaneReady(instance: {
  findNode: (id: string) => Node | undefined
  nodes: Node[]
  getViewport: () => { x: number; y: number; zoom: number }
  zoomTo: (zoom: number) => void
}) {
  ;(window as unknown as { __spike: unknown }).__spike = {
    findNode: (id: string) => instance.findNode(id),
    nodeList: () => instance.nodes,
    getViewport: () => instance.getViewport(),
    zoomTo: (z: number) => instance.zoomTo(z),
  }
}
</script>

<template>
  <div class="spike-root">
    <header class="spike-topbar">
      <div class="spike-title">
        区域画布 Spike
        <span>Vue Flow 1.48 · group 分区 / extent:parent / 跨区契约边</span>
      </div>
      <div class="spike-actions">
        <button class="spike-btn" @click="reset">重置布局</button>
      </div>
    </header>

    <div class="spike-canvas">
      <VueFlow
        :nodes="nodes"
        :edges="edges"
        :node-types="nodeTypes"
        :default-viewport="{ zoom: 0.92 }"
        :min-zoom="0.3"
        :max-zoom="1.8"
        :snap-to-grid="true"
        :snap-grid="[8, 8]"
        fit-view-on-init
        @nodes-change="onNodesChange"
        @edges-change="onEdgesChange"
        @connect="onConnect"
        @node-click="onNodeClick"
        @pane-ready="onPaneReady"
      >
        <Background :gap="22" :size="1.4" color="#232a33" />
        <Controls />
        <MiniMap pannable zoomable :node-color="miniColor" />
      </VueFlow>

      <div class="spike-hint">
        <div><b>验证点</b></div>
        <div>① 拖动文件卡片向区域外 —— 应被钳制在区域边框内</div>
        <div>② 拖动区域标题栏 —— 区内文件整体跟随</div>
        <div>③ 拖卡片右侧圆点到另一张卡片 —— 新建连线</div>
        <div>④ 滚轮缩放 / 空白拖动画布 / 右下角小地图导航</div>
        <div v-if="selectedId" class="spike-selected">当前选中：{{ selectedId }}</div>
      </div>
    </div>
  </div>
</template>
