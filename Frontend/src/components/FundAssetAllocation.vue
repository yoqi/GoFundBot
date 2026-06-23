<template>
  <div class="asset-allocation-card">
    <div class="card-header">
      <h3>📊 资产配置</h3>
    </div>
    <div class="card-body">
      <div v-if="hasData" class="allocation-content">
        <div class="chart-container">
          <div v-if="hasLeverage" class="leverage-badge">⚠ 该基金存在杠杆</div>
          <div ref="chartEl" class="allocation-chart"></div>
        </div>
        <div class="legend-info">
          <div v-for="(serie, index) in displaySeries" :key="index" class="legend-item">
            <span class="legend-dot" :style="{ background: getBarColor(serie.name, getBarIndex(serie.name)) }"></span>
            <span class="legend-name">{{ serie.name }}</span>
            <span class="legend-value">{{ formatValue(serie.data[serie.data.length - 1], serie.name) }}</span>
          </div>
        </div>
      </div>
      <div v-else class="no-data">
        <p>暂无资产配置数据</p>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

export default {
  name: 'FundAssetAllocation',
  props: {
    assetAllocation: {
      type: Object,
      default: () => ({})
    }
  },
  setup(props) {
    const chartEl = ref(null)
    let chartInstance = null

    const colors = ['#1677ff', '#52c41a', '#faad14', '#ff4d4f', '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc']
    const otherColor = '#bfbfbf'

    // 去掉名称中的"占净比"后缀
    const cleanName = (name) => {
      if (!name) return name
      return name.replace(/占净比$/, '')
    }

    // 处理资产配置数据：补全"其他"、检测杠杆
    const processedAllocation = computed(() => {
      const rawSeries = props.assetAllocation?.series || []
      const cats = props.assetAllocation?.categories || []

      if (!cats.length || !rawSeries.length) {
        return { categories: [], series: [], hasLeverage: false, hasOther: false }
      }

      // 深拷贝并清理名称
      const cleanedSeries = rawSeries.map(s => ({
        ...s,
        name: cleanName(s.name),
        data: [...(s.data || [])]
      }))

      // 分离百分比系列和净资产
      const pctSeries = cleanedSeries.filter(s => s.name !== '净资产')
      const netAssetIdx = cleanedSeries.findIndex(s => s.name === '净资产')

      // 计算每个报告期的"其他"补全值
      const numPeriods = cats.length
      const otherData = []
      let hasLeverage = false

      for (let i = 0; i < numPeriods; i++) {
        let sum = 0
        for (const serie of pctSeries) {
          sum += parseFloat(serie.data[i]) || 0
        }

        if (sum > 100.01) {
          hasLeverage = true
          otherData.push(0)
        } else if (sum < 99.99) {
          otherData.push(parseFloat((100 - sum).toFixed(2)))
        } else {
          otherData.push(0)
        }
      }

      // 只要任一报告期需要补全，就添加"其他"系列
      const hasOther = otherData.some(v => v > 0)

      if (hasOther) {
        const otherSeries = {
          name: '其他',
          type: null,
          data: otherData,
          yAxis: 0
        }
        // "其他"插在百分比类最后、净资产之前
        if (netAssetIdx >= 0) {
          cleanedSeries.splice(netAssetIdx, 0, otherSeries)
        } else {
          cleanedSeries.push(otherSeries)
        }
      }

      return { categories: cats, series: cleanedSeries, hasLeverage, hasOther }
    })

    const categories = computed(() => processedAllocation.value.categories)
    const series = computed(() => processedAllocation.value.series)
    const hasData = computed(() => categories.value.length > 0 && series.value.length > 0)
    const hasLeverage = computed(() => processedAllocation.value.hasLeverage)

    // 柱状图颜色："其他"固定灰色，其余按非"其他"顺序分配颜色（保持插入前后颜色一致）
    const getBarColor = (name, _index) => {
      if (name === '其他') return otherColor
      const nonOtherBarNames = series.value
        .filter(s => s.name !== '净资产' && s.name !== '其他')
        .map(s => s.name)
      const nonOtherIdx = nonOtherBarNames.indexOf(name)
      return nonOtherIdx >= 0 ? colors[nonOtherIdx % colors.length] : colors[0]
    }

    const getBarIndex = (name) => {
      const barNames = series.value.filter(s => s.name !== '净资产').map(s => s.name)
      return barNames.indexOf(name)
    }

    const formatValue = (value, name) => {
      if (value === null || value === undefined) return '--'
      if (name === '净资产') {
        return value + '亿'
      }
      return value + '%'
    }

    const initChart = () => {
      if (!chartEl.value || !hasData.value) return

      if (chartInstance) {
        chartInstance.dispose()
      }

      chartInstance = echarts.init(chartEl.value)

      // 准备柱状图数据（排除净资产，它用折线图）
      const barSeries = series.value
        .filter(s => s.name !== '净资产')
        .map((serie, index) => ({
          name: serie.name,
          type: 'bar',
          stack: 'total',
          data: serie.data,
          itemStyle: {
            color: getBarColor(serie.name, index)
          },
          label: {
            show: true,
            position: 'inside',
            formatter: (p) => p.value > 5 ? p.value + '%' : '' // Show label if wide enough
          }
        }))

      // 净资产用折线图
      const netAssetSerie = series.value.find(s => s.name === '净资产')
      const lineSeries = netAssetSerie ? [{
        name: '净资产',
        type: 'line',
        yAxisIndex: 1, // 使用右侧Y轴
        data: netAssetSerie.data,
        itemStyle: {
          color: '#ee6666'
        },
        lineStyle: {
            width: 3
        },
        symbol: 'circle',
        symbolSize: 8
      }] : []

      const option = {
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            type: 'shadow'
          },
          formatter: (params) => {
            let result = `<div style="font-weight: bold; margin-bottom: 8px;">${params[0].axisValue}</div>`
            params.forEach(param => {
              const unit = param.seriesName === '净资产' ? '亿' : '%'
              result += `<div style="margin: 4px 0;">
                <span style="display:inline-block;margin-right:5px;border-radius:50%;width:10px;height:10px;background-color:${param.color};"></span>
                ${param.seriesName}: <strong>${param.value}${unit}</strong>
              </div>`
            })
            return result
          }
        },
        legend: {
          data: series.value.map(s => s.name),
          bottom: 0,
          type: 'scroll'
        },
        grid: {
          left: '3%',
          right: '5%',
          bottom: '10%',
          top: '15%',
          containLabel: true
        },
        xAxis: {
          type: 'category',
          data: categories.value,
          boundaryGap: true
        },
        yAxis: [
            {
                type: 'value',
                axisLabel: { formatter: '{value}%' },
                splitLine: { show: true }
            },
            {
                type: 'value',
                name: '净资产(亿)',
                position: 'right',
                axisLabel: { formatter: '{value}' },
                splitLine: { show: false }
            }
        ],
        series: [...barSeries, ...lineSeries]
      }

      chartInstance.setOption(option)
    }

    onMounted(() => {
      nextTick(() => {
        initChart()
      })
    })

    watch(() => props.assetAllocation, () => {
      nextTick(() => {
        initChart()
      })
    }, { deep: true })

    // 用于显示的系列（排除净资产）
    const displaySeries = computed(() => series.value.filter(s => s.name !== '净资产'))

    return {
      chartEl,
      categories,
      series,
      displaySeries,
      hasData,
      hasLeverage,
      getBarColor,
      getBarIndex,
      formatValue
    }
  }
}
</script>

<style scoped>
.asset-allocation-card {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.card-header {
  background: linear-gradient(135deg, #1677ff 0%, #0958d9 100%);
  color: white;
  padding: 12px 16px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.chart-container {
  position: relative;
  flex: 1;
  min-height: 200px;
}

.leverage-badge {
  position: absolute;
  top: 4px;
  left: 8px;
  z-index: 10;
  padding: 4px 10px;
  background: #fff3cd;
  border: 1px solid #ffc107;
  border-radius: 4px;
  color: #856404;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  pointer-events: none;
}

.card-body {
  padding: 12px;
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.allocation-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}

.allocation-chart {
  width: 100%;
  height: 100%;
  min-height: 200px;
}

.legend-info {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  justify-content: center;
  padding: 8px 0;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.legend-name {
  color: #666;
}

.legend-value {
  font-weight: 600;
  color: #333;
}

.no-data {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #999;
}
</style>
