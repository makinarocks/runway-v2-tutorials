import React, { useState, useRef, useEffect } from 'react';
import { Layout, Card, Button, Upload, Progress, Alert, Statistic, Row, Col, Tabs, message, Space, Typography, Divider, Form, Input, InputNumber, Select, Slider, Switch, Popconfirm } from 'antd';
import { UploadOutlined, CloudUploadOutlined, BarChartOutlined, LineChartOutlined, PieChartOutlined, DashboardOutlined, PlayCircleOutlined, ThunderboltOutlined, SettingOutlined, ReloadOutlined } from '@ant-design/icons';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, BarChart, Bar, ReferenceLine } from 'recharts';
import dayjs from 'dayjs';
import axios from 'axios';
import Papa from 'papaparse';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;
const { TabPane } = Tabs;
const { Option } = Select;

export default function EnergyDemandMLOpsSystem() {
  const [collapsed, setCollapsed] = useState(false);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [predictionData, setPredictionData] = useState([]);
  const [actualData, setActualData] = useState([]);
  const [combinedData, setCombinedData] = useState([]);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const [metrics, setMetrics] = useState(null);
  const [retrainingProgress, setRetrainingProgress] = useState(0);
  const [isRetraining, setIsRetraining] = useState(false);
  const [csvRows, setCsvRows] = useState([]);
  const [selectedCsvRowIndex, setSelectedCsvRowIndex] = useState(null);
  const [inferenceByRow, setInferenceByRow] = useState({}); // { [rowIndex]: [{ timestamp, value }] }
  const [isBulkInferencing, setIsBulkInferencing] = useState(false);
  const [bulkInferProgress, setBulkInferProgress] = useState(0);
  const [bulkInferIndex, setBulkInferIndex] = useState(0);
  const [bulkInferTotal, setBulkInferTotal] = useState(0);
  const [metricsByRow, setMetricsByRow] = useState({}); // { [rowIndex]: Metrics }
  const [globalMetrics, setGlobalMetrics] = useState(null);
  const [timelineData, setTimelineData] = useState([]); // unified past actual + predicted + future actual for plotting
  const [lastInferredRowIndex, setLastInferredRowIndex] = useState(null);
  const [zeroTimestamp, setZeroTimestamp] = useState(null);
  const [useAfterTraining, setUseAfterTraining] = useState(false); // 재학습 이후 엔드포인트 사용 여부
  const [retrainCompleted, setRetrainCompleted] = useState(false); // 재학습 완료 후 재클릭 방지
  const [settingsOpen, setSettingsOpen] = useState(false); // 설정 창 열림 여부
  const [settingsForm] = Form.useForm();
  const [settings, setSettings] = useState({
    autoFollowSlider: true,
    accuracyThreshold: 85,
    simulateOnError: true,
  });
  const bulkAbortControllerRef = useRef(null);
  const retrainIntervalRef = useRef(null);
  const uploadSessionRef = useRef(0);
  const [sliderIndex, setSliderIndex] = useState(0); // zero-based
  const sliderIndexRef = useRef(0);
  const autoFollowRef = useRef(true); // 슬라이더가 우측 끝(최신)에 있을 때 true
  const [actualSeriesByBaseTs, setActualSeriesByBaseTs] = useState({}); // base timestamp별 실측 묶음 저장
  const [actualPointsCount, setActualPointsCount] = useState(0);
  const [actualSeriesByIndex, setActualSeriesByIndex] = useState([]); // 업로드된 실측의 행 인덱스 정렬 미래 실측(1..72)
  const [cachedPredictionRows, setCachedPredictionRows] = useState(null); // 추론 데이터 업로드에서 생성된 API 인풋 캐시
  const [lastUploadType, setLastUploadType] = useState(null); // 마지막 업로드 타입 ('prediction' 또는 'actual')

  // API 설정 — Runway 2.0: 설정 패널에서 사용자가 입력
  const [apiSettings, setApiSettings] = useState({
    apiKey: '',               // Runway API 토큰 (OpenBao runway_api_key)
    inferenceEndpoint: '',    // https://inference.<domain>/api/<proj>/<ep>/<deploy>
    deploymentId: 'default',  // KServe V2 model name (Runway MLServer 고정값)
    airflowUrl: '',           // https://airflow.<domain> (로컬: /api/airflow)
    airflowToken: '',         // Airflow Bearer 토큰 (브라우저 DevTools 에서 복사)
    dagId: '',                // energy_demand_prediction_<project-id>
  });
  const [apiSettingsForm] = Form.useForm();
  const [apiSettingsOpen, setApiSettingsOpen] = useState(true); // 첫 로드시 API 설정 열기

  // KServe V2 추론 URL 구성
  const API_ENDPOINT = apiSettings.inferenceEndpoint
    ? `${apiSettings.inferenceEndpoint.replace(/\/+$/, '')}/v2/models/${apiSettings.deploymentId}/infer`
    : '';
  const API_KEY_TOKEN = apiSettings.apiKey;

  // 샘플 데이터 생성
  const generateSampleData = (type = 'prediction') => {
    const data = [];
    const baseDate = dayjs().subtract(24, 'hour');
    
    for (let i = 0; i < 24; i++) {
      const timestamp = baseDate.add(i, 'hour').format('YYYY-MM-DD HH:mm');
      const baseValue = 1000 + Math.sin(i * Math.PI / 12) * 300 + Math.random() * 100;
      
      if (type === 'prediction') {
        data.push({
          timestamp,
          predicted: Number(baseValue.toFixed(2)),
          temperature: Number((20 + Math.random() * 10).toFixed(1)),
          humidity: Math.floor(50 + Math.random() * 30),
          windSpeed: Number((Math.random() * 5).toFixed(1)),
          rainfall: Number((Math.random() * 2).toFixed(1))
        });
      } else {
        data.push({
          timestamp,
          actual: Number((baseValue + (Math.random() - 0.5) * 200).toFixed(2)),
          temperature: Number((20 + Math.random() * 10).toFixed(1)),
          humidity: Math.floor(50 + Math.random() * 30)
        });
      }
    }
    return data;
  };

  // 성능 메트릭 계산
  const calculateMetrics = (predicted, actual) => {
    if (!Array.isArray(predicted) || !Array.isArray(actual)) return null;
    if (predicted.length !== actual.length || predicted.length === 0) return null;

    // 유효한 쌍만 필터링 (숫자이며 실측 0이 아닌 경우)
    const pairs = [];
    for (let i = 0; i < predicted.length; i++) {
      const pred = Number(predicted[i]?.predicted);
      const act = Number(actual[i]?.actual);
      if (!Number.isFinite(pred) || !Number.isFinite(act) || act === 0) continue;
      pairs.push([pred, act]);
    }
    if (pairs.length === 0) return null;

    let mapeSum = 0;
    let maeSum = 0;
    let maxError = 0;
    let validPairs = 0;

    for (const [pred, act] of pairs) {
      const error = Math.abs(pred - act);
      const errorRate = Math.abs(act) > 0 ? (error / Math.abs(act)) * 100 : 0;

      // 유효한 값만 합산
      if (Number.isFinite(errorRate) && Number.isFinite(error)) {
        mapeSum += errorRate;
        maeSum += error;
        if (errorRate > maxError) maxError = errorRate;
        validPairs++;
      }
    }

    if (validPairs === 0) return null;

    const mape = mapeSum / validPairs;
    const mae = maeSum / validPairs;

    // NaN이나 무한대 값 방지
    const safeMape = Number.isFinite(mape) ? mape : 100;
    const safeMae = Number.isFinite(mae) ? mae : 0;
    const safeMaxError = Number.isFinite(maxError) ? maxError : 100;

    const accuracy = Math.max(0, Math.min(100, 100 - safeMape));

    return {
      accuracy: Number(accuracy.toFixed(2)),
      mape: Number(safeMape.toFixed(2)),
      mae: Number(safeMae.toFixed(2)),
      maxError: Number(safeMaxError.toFixed(2))
    };
  };





  // 예측 데이터 업로드 처리
  const handlePredictionUpload = (file) => {
    const sessionId = ++uploadSessionRef.current;
    setIsUploading(true);
    setUploadProgress(10);
    // 슬라이더 초기화 - 새 데이터에 맞게 설정 (runBulkInferenceForAllRows에서 최종적으로 설정됨)
    setSliderIndex(0);
    sliderIndexRef.current = 0;

    Papa.parse(file, {
      header: true,
      dynamicTyping: true,
      skipEmptyLines: true,
      complete: (results) => {
        if (sessionId !== uploadSessionRef.current) return; // 오래된 업로드 무시
        try {
          const rows = (results?.data || []).filter(r => Object.keys(r).length > 0);
          if (!rows.length) {
            message.error('CSV에 유효한 데이터가 없습니다.');
            setIsUploading(false);
            return;
          }

          // 추론 데이터 업로드 시에는 API에 필요한 값만 남기고 (0:72 실측 컬럼 제거)
          const filteredRows = rows.map(filterRowForApiPayload);
          setCsvRows(filteredRows);
          setCachedPredictionRows(filteredRows); // API 인풋 캐시에 저장
          setSelectedCsvRowIndex(null);
          setActualData([]);
          setPredictionData([]);
          setCombinedData([]);
          setMetrics(null);
          setMetricsByRow({});
          setGlobalMetrics(null);
          setTimelineData([]);
          setZeroTimestamp(null);
          setLastInferredRowIndex(null);
          setActualSeriesByBaseTs({});
          setActualPointsCount(0);
          setLastUploadType('prediction'); // 업로드 타입 설정
          // 첫 업로드 시 플롯 상태 초기화
          setTimelineData([]);
          setPredictionData([]);
          setActualData([]);
          setCombinedData([]);
          setMetrics(null);

          setUploadProgress(100);
          setIsUploading(false);
          message.success('추론용 CSV가 업로드되었습니다. 모든 행에 대해 자동으로 추론을 진행합니다.');

          // 자동 일괄 추론 실행
          runBulkInferenceForAllRows(filteredRows, null);
        } catch (e) {
          console.error(e);
          setIsUploading(false);
          message.error('CSV 파싱 중 오류가 발생했습니다.');
        }
      },
      error: (err) => {
        console.error(err);
        setIsUploading(false);
        message.error('CSV 읽기 중 오류가 발생했습니다.');
      }
    });

    return false; // prevent default upload
  };

  // 실측 데이터 업로드 처리
  const handleActualUpload = (file) => {
    setSliderIndex(0);
    sliderIndexRef.current = 0;
    autoFollowRef.current = true;
    // 기존 추론 결과는 유지 (재사용)
    setIsUploading(true);
    setUploadProgress(10);
    Papa.parse(file, {
      header: true,
      dynamicTyping: true,
      skipEmptyLines: true,
      complete: (results) => {
        try {
          const rows = (results?.data || []).filter(r => Object.keys(r).length > 0);
          if (!rows.length) {
            message.error('CSV에 유효한 데이터가 없습니다.');
            setIsUploading(false);
            return;
          }

          // 스키마 검증: 필수 컬럼 존재 여부 확인
          const hasBaseColumns = rows.some(r => r?.['날짜'] && r?.['시간'] != null);
          const hasAnyFutureActual = rows.some(r => {
            for (let i = 1; i <= 72; i++) {
              if (Object.prototype.hasOwnProperty.call(r, `열수요실적_pred_${i}`)) return true;
              if (Object.prototype.hasOwnProperty.call(r, `열수요실적_${i}`)) return true;
            }
            return false;
          });

          if (!hasBaseColumns) {
            message.error('필수 컬럼 누락: 날짜/시간 컬럼이 필요합니다.');
            setIsUploading(false);
            return;
          }

          if (!hasAnyFutureActual) {
            message.error('미래 실측 컬럼(열수요실적_pred_1..72 또는 열수요실적_1..72)이 필요합니다.');
            setIsUploading(false);
            return;
          }

          // 모든 행을 base timestamp별로 묶어 과거(-24..0) 및 미래(0..72) 실측을 수집
          const map = {};
          let totalFuturePoints = 0;
          const futureByIndex = [];
          for (let idx = 0; idx < rows.length; idx++) {
            const r = rows[idx];
            const base = buildBaseDateFromRow(r);
            if (!base || !base.isValid()) {
              console.warn(`행 ${idx}에서 유효한 날짜/시간을 파싱할 수 없습니다:`, r);
              continue;
            }
            const baseKey = base.format('YYYY-MM-DD HH:mm');

            // 과거 실측: -24..0
            const pastActual = [];
            for (let i = -24; i <= 0; i++) {
              const col = `열수요실적_${i}`;
              if (Object.prototype.hasOwnProperty.call(r, col)) {
                const val = toNumber(r[col]);
                if (val != null) {
                  pastActual.push({ timestamp: base.add(i, 'hour').format('YYYY-MM-DD HH:mm'), actual: val });
                }
              }
            }

            // 미래 실측: 1..72 (우선순위: 열수요실적_pred_i, fallback: 열수요실적_i)
            const futureActual = [];
            for (let i = 1; i <= 72; i++) {
              const key1 = `열수요실적_pred_${i}`;
              const key2 = `열수요실적_${i}`;
              let raw = null;
              if (Object.prototype.hasOwnProperty.call(r, key1)) raw = r[key1];
              else if (Object.prototype.hasOwnProperty.call(r, key2)) raw = r[key2];
              const val = toNumber(raw);
              if (val != null) {
                futureActual.push({ timestamp: base.add(i, 'hour').format('YYYY-MM-DD HH:mm'), actual: val });
              }
            }

            if (pastActual.length || futureActual.length) {
              map[baseKey] = { pastActual, futureActual };
              totalFuturePoints += futureActual.length;
              futureByIndex.push({ index: idx, futureActual });
            }
          }

          // 상태 업데이트 전 데이터 검증
          if (Object.keys(map).length === 0) {
            message.warning('유효한 날짜/시간 데이터를 가진 행이 없습니다. CSV 형식을 확인해주세요.');
            setIsUploading(false);
            return;
          }

          setActualSeriesByBaseTs(map);
          setActualSeriesByIndex(futureByIndex.sort((a, b) => (a?.index ?? 0) - (b?.index ?? 0)));
          setActualPointsCount(totalFuturePoints);
          // 실제 추론용으로 필요한 컬럼만 남긴 행 배열 구성
          let filteredRows = [];
          let reusedFromCache = false;

          // 캐시된 추론 데이터가 있고 행 수가 동일하다면 재활용
          if (cachedPredictionRows && cachedPredictionRows.length === rows.length) {
            console.log('실측 데이터 업로드: 캐시된 추론 데이터를 재활용합니다.');
            filteredRows = cachedPredictionRows;
            reusedFromCache = true;
            message.info('이전에 업로드된 추론 데이터를 재활용하여 처리 속도가 향상되었습니다.');
          } else {
            try {
              filteredRows = rows.map(filterRowForApiPayload);
            } catch (filterError) {
              console.error('행 필터링 중 오류:', filterError);
              message.warning('일부 행에서 필터링 오류가 발생했으나 계속 진행합니다.');
              filteredRows = rows.map(r => r || {}); // 기본값 사용
            }
          }

          // 뷰 대상 CSV를 실측 업로드 기준으로 갱신
          if (!Object.keys(inferenceByRow).length) {
            // 기존 추론 결과가 없으면 행 데이터도 갱신
            setCsvRows(filteredRows);
          }
          setLastUploadType('actual');

          // 기존 추론 결과가 있으면 재추론 없이 실측만 매칭하여 메트릭 계산
          const existingInference = inferenceByRow;
          if (Object.keys(existingInference).length > 0) {
            console.log('[실측 업로드] 기존 추론 결과 재사용 — 실측만 매칭');
            // 기존 추론 + 새 실측으로 메트릭 재계산
            const nextMetricsByRow = {};
            const aggPred = [];
            const aggAct = [];

            for (const [idxStr, predSeries] of Object.entries(existingInference)) {
              const idx = Number(idxStr);
              const row = csvRows[idx] || filteredRows[idx];
              if (!row || !predSeries?.length) continue;
              const base = buildBaseDateFromRow(row);
              const baseKey = base ? base.format('YYYY-MM-DD HH:mm') : null;
              const bundle = baseKey ? map[baseKey] : null;
              const actualSeries = bundle?.futureActual ?? [];

              if (actualSeries.length) {
                const predChart = predSeries.map(p => ({ timestamp: p.timestamp, predicted: p.value }));
                const merged = mergeHorizonSeries(predChart, actualSeries);
                const rowMetrics = calculateMetrics(
                  merged.map(m => ({ predicted: m.predicted })),
                  merged.map(m => ({ actual: m.actual }))
                );
                nextMetricsByRow[idx] = rowMetrics;
                for (const m of merged) {
                  if (m.predicted != null && m.actual != null) {
                    aggPred.push({ predicted: m.predicted });
                    aggAct.push({ actual: m.actual });
                  }
                }
              }
            }

            setMetricsByRow(nextMetricsByRow);
            if (aggPred.length && aggAct.length) {
              setGlobalMetrics(calculateMetrics(aggPred, aggAct));
            }

            // 현재 선택된 행의 뷰 갱신 — state 반영 전이므로 실측 데이터를 직접 전달
            const viewIdx = sliderIndexRef.current || 0;
            const viewRow = csvRows[viewIdx] || filteredRows[viewIdx];
            const viewBase = buildBaseDateFromRow(viewRow);
            const viewBaseKey = viewBase ? viewBase.format('YYYY-MM-DD HH:mm') : null;
            const viewBundle = viewBaseKey ? map[viewBaseKey] : null;
            setTimeout(() => {
              refreshSelectionView(viewIdx, {
                predSeries: existingInference[viewIdx],
                rowOverride: viewRow,
                actualSeries: viewBundle?.futureActual ?? [],
                pastActualSeries: viewBundle?.pastActual ?? buildPastActualFromRow(viewRow),
              });
              setForceRefresh(prev => prev + 1);
            }, 100);
            message.success('실측 데이터가 추가되었습니다. 기존 예측값과 비교 메트릭이 갱신되었습니다.');
          } else {
            // 기존 추론 결과가 없으면 추론 실행
            setCsvRows(filteredRows);
            setTimelineData([]);
            setPredictionData([]);
            setActualData([]);
            setCombinedData([]);
            setMetrics(null);
            message.success('실측 CSV가 업로드되었습니다. 추론을 실행합니다.');
            runBulkInferenceForAllRows(filteredRows, null).catch((inferenceError) => {
              console.error('실측 데이터 일괄 추론 중 오류:', inferenceError);
              message.warning('실측 데이터는 업로드되었으나 추론에 실패했습니다.');
            });
          }
        } catch (e) {
          console.error(e);
          message.error('CSV 파싱 중 오류가 발생했습니다.');
        } finally {
          setUploadProgress(100);
          setIsUploading(false);
        }
      },
      error: (err) => {
        console.error(err);
        setIsUploading(false);
        message.error('CSV 읽기 중 오류가 발생했습니다.');
      }
    });
    return false;
  };

  // CSV 행 변경 처리
  const handleCsvRowChange = (idx) => {
    // 유지: 메트릭 탭에서 카드 클릭 시 해당 행을 임시로 볼 수 있게 하되,
    // 예측 탭에서는 항상 마지막 인퍼런스 결과를 표시하므로 selectedCsvRowIndex는 별도 표시용으로만 사용
    setSelectedCsvRowIndex(idx);
    refreshSelectionView(idx);
  };

  // 개별 행 추론 액션 제거 (일괄 추론으로 대체)

  // 모든 행에 대해 자동 일괄 추론 수행
  const runBulkInferenceForAllRows = async (rows, preferredIndex = null, options = {}) => {
    const { suppressCancelMessage = false, forceAfterTraining = false } = options;
    try {
      // 입력 검증
      if (!rows || !Array.isArray(rows) || rows.length === 0) {
        console.error('runBulkInferenceForAllRows: 유효하지 않은 rows 입력:', rows);
        throw new Error('유효하지 않은 행 데이터입니다.');
      }
      // 이전 일괄 추론이 있다면 취소
      if (bulkAbortControllerRef.current && !bulkAbortControllerRef.current.signal.aborted) {
        bulkAbortControllerRef.current.abort();
      }
      const controller = new AbortController();
      bulkAbortControllerRef.current = controller;
      setIsBulkInferencing(true);
      setBulkInferProgress(5);
      setBulkInferIndex(0);
      setBulkInferTotal(rows.length);

      const headers = {
        accept: 'application/json',
        'Content-Type': 'application/json',
        authorization: `Bearer ${API_KEY_TOKEN}`
      };
      const endpoint = API_ENDPOINT;

      // 1) 배치 payload 구성 (pd 형식, shape = [배치크기])
      const batchPayload = buildPdBatchPayloadFromRows(rows);

      // 2) 단일 API 호출로 일괄 추론
      const resp = await axios.post(endpoint, batchPayload, { headers, signal: controller.signal });
      setBulkInferProgress(70);

      // 3) 응답에서 각 행별 72-step horizon 복원
      let seriesByRow = extractBatchHorizonFromResponse(resp?.data, rows || []);
      if (!Array.isArray(seriesByRow) || seriesByRow.length === 0) {
        // Fallback: 시뮬레이션 (설정에 따라)
        try {
          if (settings?.simulateOnError) {
            seriesByRow = rows.map(r => simulateHorizonFromRow(r));
          } else {
            seriesByRow = rows.map(() => []);
          }
        } catch (fallbackError) {
          console.error('시뮬레이션 폴백 중 오류:', fallbackError);
          seriesByRow = rows.map(() => []);
        }
      }

      if (controller.signal.aborted) {
        if (!suppressCancelMessage) message.info('일괄 추론이 취소되었습니다.');
        return;
      }

      // 4) 상태 일괄 갱신 (모든 행)
      const inferenceMap = {};
      const nextMetricsByRow = {};
      const aggPred = [];
      const aggAct = [];

      for (let i = 0; i < rows.length; i++) {
        const predSeries = seriesByRow[i] || [];
        inferenceMap[i] = predSeries;

        const actualSeries = getActualFutureFromRow(rows[i]);
        if (actualSeries && actualSeries.length) {
          const predChart = predSeries.map(p => ({ timestamp: p.timestamp, predicted: p.value }));
          const merged = mergeHorizonSeries(predChart, actualSeries);
          const rowMetrics = calculateMetrics(
            merged.map(m => ({ predicted: m.predicted })),
            merged.map(m => ({ actual: m.actual }))
          );
          nextMetricsByRow[i] = rowMetrics;

          for (const m of merged) {
            if (m.predicted != null && m.actual != null) {
              aggPred.push({ predicted: m.predicted });
              aggAct.push({ actual: m.actual });
            }
          }
        }
      }

      setInferenceByRow(inferenceMap);
      setMetricsByRow(nextMetricsByRow);

      if (aggPred.length && aggAct.length) {
        setGlobalMetrics(calculateMetrics(aggPred, aggAct));
      } else {
        // 교집합으로 재계산 시도
        const allPred = [];
        const allAct = [];
        if (actualSeriesByIndex.length) {
          for (const { index, futureActual } of actualSeriesByIndex) {
            const ps = inferenceMap[index] || [];
            if (!ps.length || !futureActual.length) continue;
            const pc = ps.map(p => ({ timestamp: p.timestamp, predicted: p.value }));
            const merged2 = mergeHorizonSeries(pc, futureActual);
            for (const m of merged2) {
              if (m.predicted != null && m.actual != null) {
                allPred.push({ predicted: m.predicted });
                allAct.push({ actual: m.actual });
              }
            }
          }
        }
        if (allPred.length && allAct.length) setGlobalMetrics(calculateMetrics(allPred, allAct));
        else setGlobalMetrics(null);
      }

      const viewIndex = (preferredIndex != null) ? preferredIndex : (rows.length > 0 ? rows.length - 1 : 0);
      setLastInferredRowIndex(rows.length > 0 ? rows.length - 1 : null);
      setBulkInferIndex(rows.length);
      setBulkInferProgress(100);

      // 모든 버튼 로직 처리 이후 표시 행을 0번으로 설정
      if (rows.length > 0) {
        setSliderIndex(0);
        sliderIndexRef.current = 0;
        setSelectedCsvRowIndex(0);
        // 상태 업데이트 후 플롯 갱신을 위해 약간의 지연
        setTimeout(() => {
          // 실측 데이터를 직접 전달하여 상태 의존성 문제 해결
          const actualSeriesForRow = getActualFutureFromRow(rows[0]);
          const pastActualForRow = buildPastActualFromRow(rows[0]);
          refreshSelectionView(0, {
            predSeries: inferenceMap[0],
            rowOverride: rows[0],
            actualSeries: actualSeriesForRow,
            pastActualSeries: pastActualForRow
          });
          // 첫 업로드 후 플롯 강제 갱신 트리거
          setForceRefresh(prev => prev + 1);
        }, 100);
      }
    } catch (err) {
      if (err?.code === 'ERR_CANCELED') {
        if (!suppressCancelMessage) message.info('일괄 추론이 취소되었습니다.');
      } else {
        console.error('배치 일괄 추론 실패:', err);
        message.error('배치 추론 중 오류가 발생했습니다.');
        // 오류 발생 시에도 기본 상태는 유지
        try {
          setInferenceByRow({});
          setMetricsByRow({});
          setGlobalMetrics(null);
        } catch (stateError) {
          console.error('상태 초기화 중 오류:', stateError);
        }
      }
    } finally {
      try {
        setIsBulkInferencing(false);
        if (bulkAbortControllerRef.current && bulkAbortControllerRef.current.signal.aborted) {
          // keep reference for potential checks
        } else {
          bulkAbortControllerRef.current = null;
        }
      } catch (finallyError) {
        console.error('finally 블록 오류:', finallyError);
      }
    }
  };

  const handleSliderChange = (value) => {
    // 현재 처리된 개수 기준으로 우측 끝 여부 판별 및 값 보정
    const processedCount = Math.max(
      bulkInferIndex,
      (lastInferredRowIndex != null ? lastInferredRowIndex + 1 : 0),
      Object.keys(inferenceByRow).length
    );
    const sliderMax = Math.max(processedCount - 1, 0);
    const clamped = Math.min(value, sliderMax);
    setSliderIndex(clamped);
    sliderIndexRef.current = clamped;
    autoFollowRef.current = (processedCount === 0) ? true : (clamped === processedCount - 1);
    // 선택된 인덱스로 뷰 갱신
    setSelectedCsvRowIndex(clamped);
    refreshSelectionView(clamped);
  };




  // 선택된 행의 예측/실측/메트릭으로 화면 갱신
  const refreshSelectionView = (index, overrides = {}) => {
    const rowOverride = overrides.rowOverride;
    const row = rowOverride ?? (csvRows && csvRows[index]);
    if (!row) return;
    const base = buildBaseDateFromRow(row);
    setZeroTimestamp(base ? base.format('YYYY-MM-DD HH:mm') : null);
    const pred = (overrides.predSeries ?? inferenceByRow[index] ?? []);
    const predChart = pred.map(p => ({ timestamp: p.timestamp, predicted: p.value }));

    // 실측 매핑에서 해당 base 시각의 과거/미래 실측을 조회
    const baseKey = base ? base.format('YYYY-MM-DD HH:mm') : null;
    const bundle = baseKey ? actualSeriesByBaseTs[baseKey] : null;
    const futureActualSeries = overrides.actualSeries ?? (bundle?.futureActual ?? []);
    if (futureActualSeries.length) {
      setActualData(futureActualSeries.map(d => ({ timestamp: d.timestamp, actual: d.actual })));
      const merged = mergeHorizonSeries(predChart, futureActualSeries);
      setCombinedData(merged);
      const calc = calculateMetrics(
        merged.map(m => ({ predicted: m.predicted })),
        merged.map(m => ({ actual: m.actual }))
      );
      setMetrics(calc);
    } else {
      // 실측이 없으면 이전 메트릭/결합 데이터가 남지 않도록 초기화
      setActualData([]);
      setCombinedData([]);
      setMetrics(null);
    }

    // Build unified timeline series: past actual (-24..0), predicted (+1..+72), future actual (+1..+72)
    const pastActualArr = (overrides.pastActualSeries?.length
      ? overrides.pastActualSeries.map(d => ({ timestamp: d.timestamp, actual: d.actual }))
      : bundle?.pastActual?.length
        ? bundle.pastActual.map(d => ({ timestamp: d.timestamp, actual: d.actual }))
        : buildPastActualFromRow(row).map(d => ({ timestamp: d.timestamp, actual: d.actual }))
    );
    const futureActual = futureActualSeries.map(d => ({ timestamp: d.timestamp, actual: d.actual }));

    // 연결: 예측 시작점(+1)의 앞에 0시각의 실측값을 보강해 끊김 없이 표시
    const zeroPoint = base ? pastActualArr.find(p => p.timestamp === base.format('YYYY-MM-DD HH:mm')) : null;
    let predForTimeline = predChart;
    if (zeroPoint) {
      const firstPredTs = predChart.length ? predChart[0].timestamp : null;
      if (firstPredTs && dayjs(firstPredTs).isAfter(dayjs(zeroPoint.timestamp))) {
        predForTimeline = [{ timestamp: zeroPoint.timestamp, predicted: zeroPoint.actual }, ...predChart];
      }
    }

    const unified = unifyTimelineSeries(pastActualArr, predForTimeline, futureActual);
    setTimelineData(unified);
    // "추론" 탭에서도 0시각에서 끊기지 않도록 연결된 예측 시리즈로 표시
    setPredictionData(predForTimeline);
  };

  // 예측 탭으로 전환 시, 현재 데이터가 비어있을 때만 마지막 인퍼런스 결과로 초기 표시
  useEffect(() => {
    // 탭 전환 시 최신 상태로 즉시 갱신
    if (activeTab === 'comparison') {
      const idx = (sliderIndexRef.current != null) ? sliderIndexRef.current : lastInferredRowIndex;
      if (idx != null) refreshSelectionView(idx);
    }
  }, [activeTab]);

  // 일괄 추론 진행 중에도 UI가 따라오도록 최신 인덱스로 갱신
  useEffect(() => {
    if (isBulkInferencing && autoFollowRef.current) {
      const idx = lastInferredRowIndex;
      if (idx != null) refreshSelectionView(idx);
    }
  }, [lastInferredRowIndex, isBulkInferencing]);

  // 첫 업로드 후 플롯이 제대로 갱신되지 않는 문제 해결
  const [forceRefresh, setForceRefresh] = useState(0);
  useEffect(() => {
    // 첫 업로드 후 timelineData가 설정되었고, 업로드가 완료되었으며, 강제 갱신이 필요한 경우
    if (timelineData.length > 0 && !isBulkInferencing && selectedCsvRowIndex != null && csvRows.length > 0 && forceRefresh > 0) {
      const currentRow = csvRows[selectedCsvRowIndex];
      if (currentRow) {
        const predSeries = inferenceByRow[selectedCsvRowIndex] || [];
        const actualSeries = getActualFutureFromRow(currentRow);
        const pastActualSeries = buildPastActualFromRow(currentRow);
        refreshSelectionView(selectedCsvRowIndex, {
          predSeries,
          rowOverride: currentRow,
          actualSeries,
          pastActualSeries
        });
      }
      setForceRefresh(0); // 강제 갱신 완료
    }
  }, [timelineData, csvRows, inferenceByRow, selectedCsvRowIndex, isBulkInferencing, forceRefresh]);

  // 컴포넌트 언마운트 시 클린업 (메모리 누수 방지)
  useEffect(() => {
    return () => {
      // Cleanup operations if needed
    };
  }, []);

  // 전역 메트릭을 예측/실측 변경에 따라 자동 재계산하여 UI 갱신 보장
  useEffect(() => {
    // 실제와 예측의 교집합 수집
    const allPred = [];
    const allAct = [];
    if (actualSeriesByIndex && actualSeriesByIndex.length) {
      for (const { index, futureActual } of actualSeriesByIndex) {
        const ps = inferenceByRow[index] || [];
        if (!ps.length || !futureActual?.length) continue;
        const pc = ps.map(p => ({ timestamp: p.timestamp, predicted: p.value }));
        const merged = mergeHorizonSeries(pc, futureActual);
        for (const m of merged) {
          if (m.predicted != null && m.actual != null) {
            allPred.push({ predicted: m.predicted });
            allAct.push({ actual: m.actual });
          }
        }
      }
    }
    if (allPred.length && allAct.length) {
      setGlobalMetrics(calculateMetrics(allPred, allAct));
    } else if (!isBulkInferencing) {
      // 처리 중이 아닐 때만 null로 리셋
      setGlobalMetrics(null);
    }
  }, [inferenceByRow, actualSeriesByIndex, isBulkInferencing]);


  // CSV 행에서 과거 실측(-24..0) 시계열 생성
  const buildPastActualFromRow = (row) => {
    if (!row) return [];
    const base = buildBaseDateFromRow(row);
    if (!base) return [];
    const out = [];
    for (let i = -24; i <= 0; i++) {
      const col = `열수요실적_${i}`;
      if (Object.prototype.hasOwnProperty.call(row, col)) {
        const ts = base.add(i, 'hour').format('YYYY-MM-DD HH:mm');
        const val = toNumber(row[col]);
        if (val != null) out.push({ timestamp: ts, actual: val });
      }
    }
    return out;
  };

  // 세 시계열을 타임라인으로 병합
  const unifyTimelineSeries = (pastActual, predicted, futureActual) => {
    const map = new Map();
    const ensure = (ts) => {
      if (!map.has(ts)) map.set(ts, { timestamp: ts });
      return map.get(ts);
    };
    pastActual.forEach(p => { const o = ensure(p.timestamp); o.actual = p.actual; });
    predicted.forEach(p => { const o = ensure(p.timestamp); o.predicted = p.predicted; });
    futureActual.forEach(p => { const o = ensure(p.timestamp); o.actual = p.actual; });
    const arr = Array.from(map.values());
    arr.sort((a, b) => dayjs(a.timestamp).valueOf() - dayjs(b.timestamp).valueOf());
    return arr;
  };


  // 여러 행을 한 번에 보낼 배치 Payload 구성 (pd, shape=[N])
  const buildPdBatchPayloadFromRows = (rows) => {
    if (!rows || !Array.isArray(rows)) {
      console.error('rows is not an array:', rows);
      return { parameters: { content_type: 'pd' }, inputs: [] };
    }

    // 1) 모든 입력 이름의 합집합을 구성
    const allNamesSet = new Set();
    const INT_NAMES = new Set(['시간', '요일', '공휴일']);

    const collectNamesFromRow = (row) => {
      ['시간', '요일', '연중일수비율', '공휴일'].forEach((c) => allNamesSet.add(c));
      for (let i = -24; i <= 0; i++) {
        allNamesSet.add(`열수요실적_${i}`);
      }
      for (let i = -24; i <= 0; i++) {
        allNamesSet.add(`기상청실적_${i}`);
      }
      for (let i = 1; i <= 72; i++) {
        allNamesSet.add(`기상청예측_${i}`);
      }
    };
    rows.forEach(collectNamesFromRow);

    const allNames = Array.from(allNamesSet);

    // 2) 각 입력 이름별로 길이 N의 데이터 배열 생성 (누락 시 0 패딩)
    const inputs = allNames.map((name) => {
      const isInt = INT_NAMES.has(name);
      const data = new Array(rows.length);
      for (let i = 0; i < rows.length; i++) {
        const v = rows[i]?.[name];
        let datum;
        if (v == null || v === '') datum = 0;
        else datum = isInt ? Number.parseInt(v, 10) : Number(v);
        if (!Number.isFinite(datum)) datum = 0;
        data[i] = datum;
      }
      return {
        name,
        shape: [rows.length],
        datatype: isInt ? 'INT64' : 'FP64',
        data
      };
    });

    return { parameters: { content_type: 'pd' }, inputs };
  };

  // 배치 응답에서 각 행별 Horizon 배열 추출
  const extractBatchHorizonFromResponse = (respData, rowsForTimeBase) => {
    try {
      if (!rowsForTimeBase || !Array.isArray(rowsForTimeBase)) {
        console.error('rowsForTimeBase is not an array:', rowsForTimeBase);
        return [];
      }

      // Case 1: Triton-like { outputs: [...] } 구조에서 벡터 출력 찾기
      if (respData && Array.isArray(respData.outputs)) {
        // 후보 출력: data 길이가 배치크기 또는 배치크기*72 이상인 것
        const batchSize = rowsForTimeBase.length;
        let horizonByRow = null;

        // 1-1) shape가 [batch, 72] 혹은 data가 batch*72인 단일 출력
        for (const out of respData.outputs) {
          const arr = Array.isArray(out?.data) ? flattenNumeric(out.data) : [];
          if (!arr.length) continue;
          const n = arr.length;
          if (n >= batchSize * 72) {
            horizonByRow = [];
            for (let b = 0; b < batchSize; b++) {
              const base = buildBaseDateFromRow(rowsForTimeBase[b]) || dayjs();
              const series = [];
              for (let k = 0; k < 72; k++) {
                const v = Number(arr[b * 72 + k]);
                if (!Number.isFinite(v)) continue;
                series.push({ timestamp: base.add(k + 1, 'hour').format('YYYY-MM-DD HH:mm'), value: v });
              }
              horizonByRow.push(series);
            }
            return horizonByRow;
          }
        }

        // 1-2) 다수 스칼라 출력(pred_1..72)이 벡터화되어 각 항목이 길이=batch 인 경우
        const scalarLike = [];
        for (const out of respData.outputs) {
          const name = String(out?.name ?? '');
          const m = name.match(/(pred_|t\+)(\d+)/i);
          if (!m) continue;
          const step = Number(m[2]);
          const arr = Array.isArray(out?.data) ? flattenNumeric(out.data) : [];
          if (!Number.isFinite(step) || arr.length < rowsForTimeBase.length) continue;
          scalarLike.push({ step, arr });
        }
        if (scalarLike.length) {
          scalarLike.sort((a, b) => a.step - b.step);
          const batch = Array.from({ length: rowsForTimeBase.length }).map(() => []);
          for (const { step, arr } of scalarLike.slice(0, 72)) {
            for (let b = 0; b < rowsForTimeBase.length; b++) {
              const v = Number(arr[b]);
              if (!Number.isFinite(v)) continue;
              const base = buildBaseDateFromRow(rowsForTimeBase[b]) || dayjs();
              batch[b].push({ timestamp: base.add(step, 'hour').format('YYYY-MM-DD HH:mm'), value: v });
            }
          }
          return batch;
        }
      }

      // Case 2: { predictions: [...] } 에 배치로 들어온 경우 (배치*72 길이, 또는 [batch][72])
      if (respData && respData.predictions) {
        const flat = flattenNumeric(respData.predictions);
        const batchSize = rowsForTimeBase.length;
        if (flat.length >= batchSize * 72) {
          const out = [];
          for (let b = 0; b < batchSize; b++) {
            const base = buildBaseDateFromRow(rowsForTimeBase[b]) || dayjs();
            const series = [];
            for (let k = 0; k < 72; k++) {
              const v = Number(flat[b * 72 + k]);
              if (!Number.isFinite(v)) continue;
              series.push({ timestamp: base.add(k + 1, 'hour').format('YYYY-MM-DD HH:mm'), value: v });
            }
            out.push(series);
          }
          return out;
        }
      }

      return [];
    } catch (e) {
      console.error('extractBatchHorizonFromResponse error', e);
      return [];
    }
  };


  // 시뮬레이션용 Horizon 생성 (API 실패 시)
  const simulateHorizonFromRow = (row) => {
    const base = buildBaseDateFromRow(row) || dayjs();
    const out = [];
    const seed = Number(toNumber(row?.['열수요실적_0'])) || 120;
    for (let i = 1; i <= 72; i++) {
      const v = seed + Math.sin(i * Math.PI / 12) * 15 + Math.random() * 8;
      out.push({ timestamp: base.add(i, 'hour').format('YYYY-MM-DD HH:mm'), value: Number(v.toFixed(2)) });
    }
    return out;
  };

  // 유틸: CSV 행 기준 시작 시각 계산
  const buildBaseDateFromRow = (row) => {
    try {
      const dateStr = row['날짜'];
      const hourVal = Number(row['시간']);
      if (!dateStr || Number.isNaN(hourVal)) return null;
      return dayjs(`${dateStr} ${String(hourVal).padStart(2, '0')}:00`);
    } catch {
      return null;
    }
  };

  // 유틸: 숫자 변환
  const toNumber = (v) => {
    if (v == null || v === '' || typeof v === 'undefined') return null;
    const n = Number(v);
    return Number.isNaN(n) || !Number.isFinite(n) ? null : n;
  };

  // 유틸: 중첩 배열 평탄화 + 숫자만 추출
  const flattenNumeric = (arr) => {
    const out = [];
    const walk = (x) => {
      if (Array.isArray(x)) x.forEach(walk);
      else if (typeof x === 'number') out.push(x);
      else if (x != null && !Number.isNaN(Number(x))) out.push(Number(x));
    };
    walk(arr);
    return out;
  };

  // 유틸: 예측/실측 Horizon 병합
  const mergeHorizonSeries = (predSeriesChart, actualSeries) => {
    const mapActual = new Map(actualSeries.map(a => [a.timestamp, a.actual]));
    return predSeriesChart.map(p => ({
      timestamp: p.timestamp,
      predicted: p.predicted,
      actual: mapActual.get(p.timestamp) ?? null
    }));
  };

  // 유틸: 추론용 CSV 행에서 API에 필요한 컬럼만 남기기 (0:72 실측값 제외)
  const filterRowForApiPayload = (row) => {
    const out = {};
    const copyIfExists = (key) => {
      if (Object.prototype.hasOwnProperty.call(row, key)) out[key] = row[key];
    };
    // 기본 키
    ['날짜', '시간', '요일', '연중일수비율', '공휴일'].forEach(copyIfExists);
    // 과거 타깃/기상 (-24..0)
    for (let i = -24; i <= 0; i++) {
      copyIfExists(`열수요실적_${i}`);
      copyIfExists(`기상청실적_${i}`);
    }
    // 미래 기상 예측 (1..72)
    for (let i = 1; i <= 72; i++) {
      copyIfExists(`기상청예측_${i}`);
    }
    // 의도적으로 제외: 열수요실적_pred_1..72, 열수요실적_1..72
    return out;
  };

  // 유틸: 현재 선택 행 기준의 미래 실측(1..72) 가져오기
  const getActualFutureFromRow = (row) => {
    const base = buildBaseDateFromRow(row);
    if (!base) return [];
    const key = base.format('YYYY-MM-DD HH:mm');
    const bundle = actualSeriesByBaseTs[key];
    return bundle?.futureActual ?? [];
  };

  // Runway 2.0: Airflow REST API 로 DAG trigger 하여 재학습
  const handleRetraining = async () => {
    if (retrainCompleted) {
      message.info('재학습이 이미 완료되었습니다.');
      return;
    }
    if (isRetraining) {
      message.info('재학습이 진행 중입니다.');
      return;
    }
    if (!apiSettings.airflowUrl || !apiSettings.dagId) {
      message.error('Airflow URL 과 DAG ID 를 설정 패널에서 입력해주세요.');
      setApiSettingsOpen(true);
      return;
    }

    setIsRetraining(true);
    setRetrainingProgress(0);

    try {
      // Airflow 3.0 + Keycloak OIDC: 토큰을 직접 사용 (v2 API)
      setRetrainingProgress(10);
      const airflowBase = apiSettings.airflowUrl.replace(/\/+$/, '');
      const token = apiSettings.airflowToken;

      if (!token) {
        message.error('Airflow 토큰을 설정 패널에서 입력해주세요.');
        setApiSettingsOpen(true);
        setIsRetraining(false);
        return;
      }

      // DAG trigger (Airflow 3.0 v2 API — logical_date 필수)
      setRetrainingProgress(30);
      await axios.post(
        `${airflowBase}/api/v2/dags/${apiSettings.dagId}/dagRuns`,
        {
          logical_date: new Date().toISOString(),
          conf: { train_files: "Q1.csv,Q2.csv,Q3.csv" },
        },
        { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } }
      );
      message.success('Airflow DAG 재학습이 트리거되었습니다. Airflow UI 에서 진행 상황을 확인하세요.');

      // 3. 진행률 시뮬레이션 (DAG 실행은 비동기이므로 UI 피드백용)
      const interval = setInterval(() => {
        setRetrainingProgress((prev) => {
          const currentProgress = typeof prev === 'number' ? prev : 30;
          if (currentProgress >= 100) {
            clearInterval(interval);
            setIsRetraining(false);
            setRetrainCompleted(true);
            message.info('재학습 DAG 가 실행 중입니다. 완료 후 모델을 재배포하고 추론을 다시 실행하세요.');
            return 100;
          }
          return currentProgress + 2;
        });
      }, 300);
      retrainIntervalRef.current = interval;

    } catch (err) {
      console.error('재학습 트리거 실패:', err);
      setIsRetraining(false);
      if (err.response?.status === 401 || err.response?.status === 403) {
        message.error('Airflow 인증 실패. 사용자명/비밀번호를 확인하세요.');
      } else {
        message.error(`재학습 트리거 실패: ${err.message}`);
      }
    }
  };

  // 성능 상태 색상 결정
  const getPerformanceColor = (mape) => {
    if (mape < 5) return '#52c41a';
    if (mape < 10) return '#faad14';
    return '#ff4d4f';
  };

  // 안전한 숫자 표시 헬퍼 함수
  const safeDisplayNumber = (value, defaultValue = 0) => {
    return Number.isFinite(value) ? value : defaultValue;
  };

  // 안전한 정확도 비교 헬퍼 함수
  const isAccuracyBelowThreshold = (accuracy, threshold) => {
    if (!Number.isFinite(accuracy) || !Number.isFinite(threshold)) return false;
    return accuracy <= threshold;
  };

  const sidebarItems = [
    { key: 'dashboard', icon: <DashboardOutlined />, label: '대시보드' },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider 
        collapsible 
        collapsed={collapsed} 
        onCollapse={setCollapsed}
        theme="light"
        width={250}
      >
        <div style={{ padding: '20px', textAlign: 'center', borderBottom: '1px solid #f0f0f0' }}>
          <Title level={4} style={{ margin: 0, color: '#1890ff' }}>
            ⚡ 에너지 수요 예측
          </Title>
          {!collapsed && <Text type="secondary">MLOps 시스템</Text>}
        </div>
        
        <div style={{ padding: '20px 0' }}>
          {sidebarItems.map(item => (
            <div
              key={item.key}
              onClick={() => setActiveTab(item.key)}
              style={{
                padding: '12px 24px',
                cursor: 'pointer',
                backgroundColor: activeTab === item.key ? '#e6f7ff' : 'transparent',
                borderRight: activeTab === item.key ? '3px solid #1890ff' : 'none',
                display: 'flex',
                alignItems: 'center',
                gap: '12px'
              }}
            >
              {item.icon}
              {!collapsed && <span>{item.label}</span>}
            </div>
          ))}
        </div>
      </Sider>

      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0' }}>
          <Row justify="space-between" align="middle">
            <Col>
              <Title level={3} style={{ margin: 0 }}>
                {sidebarItems.find(item => item.key === activeTab)?.label || '대시보드'}
              </Title>
            </Col>
            <Col>
              <Space>
                <Upload
                  beforeUpload={handleActualUpload}
                  showUploadList={false}
                  accept=".csv"
                >
                  <Button icon={<CloudUploadOutlined />}>
                    실측 데이터 업로드
                  </Button>
                </Upload>
                {/* 추론용 데이터 업로드: 실측 파싱/정밀도/플롯 갱신 없음 */}
                <Upload
                  beforeUpload={handlePredictionUpload}
                  showUploadList={false}
                  accept=".csv"
                >
                  <Button icon={<UploadOutlined />} type="primary">
                    추론 데이터 업로드
                  </Button>
                </Upload>
                <Button onClick={() => {
                  apiSettingsForm.setFieldsValue(apiSettings);
                  setApiSettingsOpen(true);
                }}>
                  API 설정
                </Button>
                <Button icon={<SettingOutlined />} onClick={() => {
                  settingsForm.setFieldsValue(settings);
                  setSettingsOpen(true);
                }}>
                  설정
                </Button>
              </Space>
            </Col>
          </Row>
        </Header>

        <Content style={{ padding: '24px', background: '#f5f5f5' }}>
          {/* API 설정 (Runway 2.0) */}
          {apiSettingsOpen && (
            <Card title="API 설정 (Runway 2.0)" style={{ marginBottom: 24 }} extra={
              <Button onClick={() => setApiSettingsOpen(false)}>닫기</Button>
            }>
              <Form
                form={apiSettingsForm}
                layout="vertical"
                initialValues={apiSettings}
                onFinish={(vals) => {
                  setApiSettings(vals);
                  setApiSettingsOpen(false);
                  message.success('API 설정이 저장되었습니다.');
                }}
              >
                <Row gutter={[16, 0]}>
                  <Col xs={24} md={12}>
                    <Form.Item name="apiKey" label="Runway API 토큰" rules={[{ required: true }]}>
                      <Input.Password placeholder="OpenBao runway_api_key 값" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name="inferenceEndpoint" label="추론 엔드포인트 URL" rules={[{ required: true }]}>
                      <Input placeholder="https://inference.<domain>/api/<proj>/<ep>/<deploy>" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item name="deploymentId" label="Deployment ID (KServe)">
                      <Input placeholder="default" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item name="airflowUrl" label="Airflow URL">
                      <Input placeholder="https://airflow.<domain>" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item name="dagId" label="DAG ID">
                      <Input placeholder="energy_demand_prediction_<project-id>" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={24}>
                    <Form.Item name="airflowToken" label="Airflow 토큰 (브라우저 DevTools > Network > Authorization 헤더에서 복사)">
                      <Input.Password placeholder="eyJ..." />
                    </Form.Item>
                  </Col>
                </Row>
                <Button type="primary" htmlType="submit">저장</Button>
              </Form>
            </Card>
          )}

          {/* 설정 창 */}
          {settingsOpen && (
            <Card title="설정" style={{ marginBottom: 24 }} extra={
              <Space>
                <Button onClick={() => setSettingsOpen(false)}>닫기</Button>
                <Popconfirm title="모델을 초기 상태로 리셋할까요?" okText="네" cancelText="아니오" onConfirm={() => {
                  setUseAfterTraining(false);
                  setRetrainCompleted(false);
                  setGlobalMetrics(null);
                  setMetrics(null);
                  setInferenceByRow({});
                  setPredictionData([]);
                  setActualData([]);
                  setCombinedData([]);
                  setTimelineData([]);
                  setCsvRows([]);
                  setCachedPredictionRows(null); // 캐시된 추론 데이터도 초기화
                  setLastUploadType(null); // 업로드 타입도 초기화
                  setSelectedCsvRowIndex(null);
                  setLastInferredRowIndex(null);
                  message.success('모델과 세션이 초기화되었습니다.');
                }}>
                  <Button danger>모델 리셋</Button>
                </Popconfirm>
              </Space>
            }>
              <Form
                form={settingsForm}
                layout="vertical"
                initialValues={settings}
                onFinish={(vals) => {
                  setSettings(vals);
                  autoFollowRef.current = vals.autoFollowSlider;
                  setSettingsOpen(false);
                  message.success('설정이 저장되었습니다.');
                }}
              >
                <Row gutter={[16, 16]}>
                  <Col xs={24} md={8}>
                    <Form.Item name="autoFollowSlider" label="자동 따라가기(슬라이더)" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item name="accuracyThreshold" label="재학습 표시 임계 정확도(%)" rules={[{ type: 'number', min: 0, max: 100 }] }>
                      <InputNumber style={{ width: '100%' }} min={0} max={100} />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item name="simulateOnError" label="API 오류 시 시뮬레이션 사용" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                  </Col>
                </Row>

                <Row>
                  <Col span={24}>
                    <Space>
                      <Button type="primary" htmlType="submit">저장</Button>
                      <Button onClick={() => settingsForm.resetFields()}>되돌리기</Button>
                    </Space>
                  </Col>
                </Row>
              </Form>
            </Card>
          )}
          {isUploading && (
            <Card style={{ marginBottom: '24px' }}>
              <Progress percent={uploadProgress} status="active" />
              <Text>데이터 업로드 중...</Text>
            </Card>
          )}

          {/* 항상 상단에 성능 메트릭 요약 표시 */}
          <div style={{ marginBottom: '24px' }}>
            <Row gutter={[24, 24]}>
              <Col span={12}>
                <Card title="선택 행 메트릭">
                  <Row gutter={[12, 12]}>
                    <Col span={12}>
                      <Statistic title="예측 정확도" value={safeDisplayNumber(metrics?.accuracy, 0)} suffix="%" valueStyle={{ color: metrics && Number.isFinite(metrics.accuracy) ? (metrics.accuracy >= 85 ? '#52c41a' : metrics.accuracy >= 70 ? '#faad14' : '#ff4d4f') : '#666' }} />
                    </Col>
                    <Col span={12}>
                      <Statistic title="MAPE" value={safeDisplayNumber(metrics?.mape, 0)} suffix="%" valueStyle={{ color: metrics && Number.isFinite(metrics.mape) ? getPerformanceColor(metrics.mape) : '#666' }} />
                    </Col>
                    <Col span={12}>
                      <Statistic title="MAE" value={safeDisplayNumber(metrics?.mae, 0)} suffix="kWh" />
                    </Col>
                    
                  </Row>
                </Card>
              </Col>
              <Col span={12}>
                <Card title="전체 메트릭(모든 행)">
                  <Row gutter={[12, 12]}>
                    <Col span={12}>
                      <Statistic title="전체 정확도" value={safeDisplayNumber(globalMetrics?.accuracy, 0)} suffix="%" valueStyle={{ color: globalMetrics && Number.isFinite(globalMetrics.accuracy) ? (globalMetrics.accuracy >= 85 ? '#52c41a' : globalMetrics.accuracy >= 70 ? '#faad14' : '#ff4d4f') : '#666' }} />
                    </Col>
                    <Col span={12}>
                      <Statistic title="전체 MAPE" value={safeDisplayNumber(globalMetrics?.mape, 0)} suffix="%" valueStyle={{ color: globalMetrics && Number.isFinite(globalMetrics.mape) ? getPerformanceColor(globalMetrics.mape) : '#666' }} />
                    </Col>
                    <Col span={12}>
                      <Statistic title="전체 MAE" value={globalMetrics?.mae ?? 0} suffix="kWh" />
                    </Col>
                    
                  </Row>
                  {globalMetrics?.accuracy != null && Number.isFinite(globalMetrics.accuracy) && isAccuracyBelowThreshold(globalMetrics.accuracy, settings?.accuracyThreshold ?? 85) && (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, marginTop: 12 }}>
                      {Boolean(isRetraining) && (
                        <Progress percent={safeDisplayNumber(retrainingProgress, 0)} size="small" style={{ width: 160 }} />
                      )}
                      <Button
                        type="primary"
                        icon={<ReloadOutlined />}
                        onClick={handleRetraining}
                        loading={Boolean(isRetraining)}
                        disabled={Boolean(retrainCompleted)}
                      >
                        재학습
                      </Button>
                    </div>
                  )}
                </Card>
              </Col>
            </Row>

            <Card style={{ marginTop: '16px' }}>
              {(() => {
                const processedCount = Math.max(
                  bulkInferIndex,
                  (lastInferredRowIndex != null ? lastInferredRowIndex + 1 : 0),
                  Object.keys(inferenceByRow).length
                );
                const totalRows = bulkInferTotal || csvRows.length || 0;
                const sliderTotalMax = totalRows > 0 ? totalRows - 1 : 0;
                const sliderVal = Math.min(sliderIndex, sliderTotalMax, Math.max(processedCount - 1, 0));
                // 진행률을 슬라이더 인덱스에 정렬: (processedIndex / sliderTotalMax)
                const processedIndex = Math.max(0, Math.min(sliderTotalMax, processedCount - 1));
                const processedPercent = sliderTotalMax > 0
                  ? Math.max(0, Math.min(100, (processedIndex / sliderTotalMax) * 100))
                  : (processedCount > 0 ? 100 : 0);
                const railStyle = {
                  height: 6,
                  background: `linear-gradient(to right, #91d5ff 0%, #91d5ff ${processedPercent}%, #f0f0f0 ${processedPercent}%, #f0f0f0 100%)`
                };
                const trackStyle = [{ backgroundColor: '#1890ff', height: 6 }];
                const handleStyle = [{ borderColor: '#1890ff' }];
                return (
                  <Row gutter={[16, 8]} align="middle">
                    <Col xs={24} md={20}>
                      <Slider
                        min={0}
                        max={sliderTotalMax}
                        value={sliderVal}
                        onChange={handleSliderChange}
                        disabled={totalRows === 0}
                        tipFormatter={(v) => `행 ${Number(v) + 1}`}
                        railStyle={railStyle}
                        trackStyle={trackStyle}
                        handleStyle={handleStyle}
                      />
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#666' }}>
                        <span>보기 대상 행: {totalRows > 0 ? (sliderVal + 1) : 0}</span>
                        <span>처리: {processedCount}/{totalRows || 0}</span>
                      </div>
                    </Col>
                  </Row>
                );
              })()}
            </Card>
          </div>

          {/* 차트 — 항상 표시 (대시보드와 예측을 단일 화면에 통합) */}
          <Card title={lastUploadType === 'prediction' ? '과거 실측 + 예측값' : lastUploadType === 'actual' ? '과거 실측 + 미래 실측 + 예측값' : '에너지 수요 예측'} style={{ marginBottom: '24px' }}>
              {(timelineData.length > 0 || combinedData.length > 0 || predictionData.length > 0) ? (
                <ResponsiveContainer width="100%" height={420}>
                  <LineChart data={
                    timelineData.length ? timelineData : (combinedData.length ? combinedData : predictionData)  // 항상 타임라인 데이터 우선 (과거 실측 + 예측 + 미래 실측)
                  }>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis 
                      dataKey="timestamp"
                      tickFormatter={(value) => dayjs(value).format('HH:mm')}
                    />
                    <YAxis />
                    <Tooltip 
                      labelFormatter={(value) => dayjs(value).format('YYYY-MM-DD HH:mm')}
                      formatter={(value, name) => [
                        `${Number(value).toFixed(2)} kWh`,
                        name === 'predicted' ? '예측값' : '실측값'
                      ]}
                    />
                    <Legend />
                    {/* 분기점(0시각)에 세로선 표시 */}
                    {zeroTimestamp && lastUploadType === 'actual' && (
                      <ReferenceLine x={zeroTimestamp} stroke="#999" strokeDasharray="4 2" label={{ value: 't=0', position: 'top', fill: '#666' }} />
                    )}
                    {/* 항상 예측값과 실측값 모두 표시 (타임라인 데이터에 포함된 내용에 따라) */}
                    <Line type="monotone" dataKey="actual" stroke="#ff4d4f" strokeWidth={2} name="실측값" />
                    <Line type="monotone" dataKey="predicted" stroke="#1890ff" strokeWidth={2} name="예측값" />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ textAlign: 'center', padding: '60px', color: '#999' }}>
                  <BarChartOutlined style={{ fontSize: '48px', marginBottom: '16px' }} />
                  <div>
                    {lastUploadType === 'prediction'
                      ? '추론 데이터가 처리되었습니다'
                      : lastUploadType === 'actual'
                        ? '실측 데이터가 처리되었습니다'
                        : '추론 또는 실측 데이터를 업로드하세요'
                    }
                  </div>
                </div>
              )}
            </Card>

          {/* 요약 통계 */}
          <Row gutter={[24, 24]}>
            <Col span={6}>
              <Card>
                <Statistic title="총 예측 데이터" value={Object.keys(inferenceByRow).reduce((sum, key) => sum + ((inferenceByRow[key] || []).length), 0)} suffix="건" valueStyle={{ color: '#1890ff' }} />
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic title="실측 데이터" value={actualPointsCount} suffix="건" valueStyle={{ color: '#52c41a' }} />
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic title="예측 정확도" value={safeDisplayNumber(globalMetrics?.accuracy ?? metrics?.accuracy, 0)} suffix="%" valueStyle={{ color: (globalMetrics || metrics) && Number.isFinite(globalMetrics?.mape ?? metrics?.mape) ? getPerformanceColor(safeDisplayNumber(globalMetrics?.mape ?? metrics?.mape, 0)) : '#666' }} />
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic title="평균 오차율" value={safeDisplayNumber(globalMetrics?.mape ?? metrics?.mape, 0)} suffix="%" valueStyle={{ color: (globalMetrics || metrics) && Number.isFinite(globalMetrics?.mape ?? metrics?.mape) ? getPerformanceColor(safeDisplayNumber(globalMetrics?.mape ?? metrics?.mape, 0)) : '#666' }} />
              </Card>
            </Col>
          </Row>
        </Content>
      </Layout>
    </Layout>
  );
}