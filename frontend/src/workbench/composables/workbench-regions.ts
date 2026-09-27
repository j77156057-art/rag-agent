// 工作台「分区可视化」域：分区地图面板（状态/契约校验横幅）、
// missing 卡片一键创建、已存在卡片补齐导出桩。
import { ref, type Ref } from 'vue'
import { regionsApi, FsApiError } from '../api'
import type { RegionInfo, ContractsResp } from '../api'
import { askConfirm, askAlert } from './dialogs'

export interface RegionMapState {
  loading: boolean
  error: string | null
  codeRoot: string
  regions: RegionInfo[]
  /** 契约校验结果；独立请求，失败时为 null（不影响分区状态展示） */
  contracts: ContractsResp | null
  contractsError: string | null
}

interface RegionDeps {
  selectedPath: Ref<string | null>
  loadTree: () => Promise<void>
}
let deps!: RegionDeps
export function initRegionDeps(d: RegionDeps) { deps = d }

const regionMapOpen = ref(false)
const regionMap = ref<RegionMapState>({
  loading: false,
  error: null,
  codeRoot: '',
  regions: [],
  contracts: null,
  contractsError: null,
})

/** 契约校验独立拉取（HTTP 200+{ok:false} 是正常业务结果），失败只置错误文案。 */
async function refreshContracts() {
  try {
    regionMap.value.contracts = await regionsApi.contracts()
    regionMap.value.contractsError = null
  } catch (e) {
    regionMap.value.contracts = null
    regionMap.value.contractsError = (e as FsApiError).message || '契约校验不可用。'
  }
}

async function openRegionMap() {
  regionMapOpen.value = true
  regionMap.value = {
    loading: true, error: null,
    codeRoot: regionMap.value.codeRoot,
    regions: regionMap.value.regions,
    contracts: regionMap.value.contracts,
    contractsError: regionMap.value.contractsError,
  }
  try {
    const r = await regionsApi.list()
    regionMap.value.codeRoot = r.code_root
    regionMap.value.regions = r.regions
    regionMap.value.error = null
  } catch (e) {
    regionMap.value.error = (e as FsApiError).message || '分区信息加载失败。'
  }
  // 契约校验单独失败不拖垮整块面板
  await refreshContracts()
  regionMap.value.loading = false
}

function closeRegionMap() {
  regionMapOpen.value = false
}

/** 点击分区卡片：在文件树中选中该分区目录（分区均为顶层目录，默认展开）并关闭面板。 */
function locateRegion(dir: string) {
  deps.selectedPath.value = dir
  regionMapOpen.value = false
}

/** 正在执行分区写操作的 key（创建/补齐按钮 spinner），同一时刻只允许一个 */
const busyRegionKey = ref<string | null>(null)

/** missing 卡片一键创建：确认后建目录 + 导出桩/README，并就地刷新分区状态/契约/文件树。 */
async function createRegion(r: RegionInfo): Promise<boolean> {
  const detail = [
    r.exports.length
      ? `将生成导出接口桩：${r.exports.join('、')}`
      : '该分区无对外接口文件，仅创建目录与 README.md',
    '不会改动其它分区，也不会重写 regions.json。',
  ].join('\n')
  const confirmed = await askConfirm({
    title: `创建分区：${r.name}`,
    message: `将在代码库根目录下创建 ${r.dir}/`,
    detail,
    confirmText: '创建',
  })
  if (!confirmed) return false
  busyRegionKey.value = r.key
  try {
    const resp = await regionsApi.createRegion(r.key)
    regionMap.value.regions = resp.regions
    // 面板保持打开：卡片与 DAG 节点就地翻为「已存在」，契约横幅同步更新
    await Promise.all([refreshContracts(), deps.loadTree()])
    if (resp.git_warning) {
      await askAlert({ title: `分区「${r.name}」已创建`, message: resp.git_warning })
    }
    return true
  } catch (e) {
    await askAlert({ title: '创建分区失败', message: (e as FsApiError).message || '未知错误。' })
    return false
  } finally {
    busyRegionKey.value = null
  }
}

/** 已存在卡片补齐缺失导出桩：确认后只生成缺失文件，就地刷新分区状态/契约/文件树。 */
async function fillRegionExports(r: RegionInfo): Promise<boolean> {
  const confirmed = await askConfirm({
    title: `补齐导出桩：${r.name}`,
    message: `将在 ${r.dir}/ 下生成缺失的导出接口文件`,
    detail: [`将生成：${r.missing_exports.join('、')}`, '仅补缺失文件，不改动其它内容，也不会自动提交。'].join('\n'),
    confirmText: '补齐',
  })
  if (!confirmed) return false
  busyRegionKey.value = r.key
  try {
    const resp = await regionsApi.fillExports(r.key)
    regionMap.value.regions = resp.regions
    await Promise.all([refreshContracts(), deps.loadTree()])
    return true
  } catch (e) {
    await askAlert({ title: '补齐导出桩失败', message: (e as FsApiError).message || '未知错误。' })
    return false
  } finally {
    busyRegionKey.value = null
  }
}

export function useRegionMap() {
  return {
    regionMapOpen, regionMap, openRegionMap, closeRegionMap, locateRegion,
    busyRegionKey, createRegion, fillRegionExports,
  }
}
