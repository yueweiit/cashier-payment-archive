import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";

export type Language = "zh" | "es";

const storageKey = "cashier-payment-language";

const zhToEs: Record<string, string> = {
  "出纳请款明细": "Detalle de solicitudes de pago de tesorería",
  "请款明细": "Solicitudes de pago",
  "内网归档工作台": "Centro interno de pagos y archivo",
  "加载中": "Cargando",
  "账号": "Usuario",
  "密码": "Contraseña",
  "登录": "Iniciar sesión",
  "请输入账号和密码": "Introduzca el usuario y la contraseña",
  "工作台": "Panel de trabajo",
  "每日应付": "Pagos diarios pendientes",
  "墨西哥审批": "Aprobaciones de México",
  "墨西哥审批跟进": "Seguimiento de aprobaciones de México",
  "复制双语提醒": "Copiar recordatorio bilingüe",
  "归档": "Archivo",
  "管理": "Administración",
  "退出": "Cerrar sesión",
  "当前周工作台": "Panel de la semana actual",
  "还没有批次": "Aún no hay lotes",
  "先创建一个本周草稿，再开始录入或从 Excel 导入。": "Cree primero un borrador de esta semana y después capture datos o impórtelos desde Excel.",
  "当前还没有可查看的批次，请联系管理员。": "No hay lotes disponibles para su cuenta. Póngase en contacto con el administrador.",
  "历史归档": "Archivo histórico",
  "用户管理": "Gestión de usuarios",
  "搜索账号、姓名、角色": "Buscar usuario, nombre o rol",
  "角色可查看全部 Sheet": "El rol puede ver todas las Sheets",
  "输入新密码，留空不改": "Introduzca una nueva contraseña o déjelo vacío para mantenerla",
  "创建登录账号并设置角色。业务人员只能查看已授权的 Sheet。": "Cree una cuenta y asigne un rol. Los usuarios de negocio solo pueden ver las Sheets autorizadas.",
  "请输入登录账号": "Introduzca el usuario",
  "请输入用户姓名": "Introduzca el nombre del usuario",
  "初始密码": "Contraseña inicial",
  "至少 6 位": "Mínimo 6 caracteres",
  "默认密码为 Yuewei123，用户登录后可以自行修改。": "La contraseña predeterminada es Yuewei123; el usuario puede cambiarla después de iniciar sesión.",
  "已授权 0 个 Sheet": "0 Sheets autorizadas",
  "未选择时，该用户登录后看不到任何 Sheet。": "Si no selecciona ninguna, el usuario no verá ninguna Sheet al iniciar sesión.",
  "创建后立即启用账号": "Activar la cuenta inmediatamente",
  "修改密码": "Cambiar contraseña",
  "业务人员": "Usuario de negocio",
  "财务": "Finanzas",
  "总经理": "Dirección general",
  "管理员": "Administrador",
  "当前批次": "Lote actual",
  "新建批次": "Crear lote",
  "状态": "Estado",
  "期间": "Periodo",
  "批次记录": "Registros del lote",
  "批次应付": "Total a pagar",
  "批次应付（折合人民币）": "Total a pagar (equivalente en CNY)",
  "累计已支付": "Total pagado",
  "累计已支付（折合人民币）": "Total pagado (equivalente en CNY)",
  "待付款": "Pendiente de pago",
  "待付款（折合人民币）": "Pendiente (equivalente en CNY)",
  "付款进度": "Progreso de pago",
  "批次付款进度": "Progreso de pago del lote",
  "批次各币种小计": "Subtotales del lote por moneda",
  "从上周生成本周": "Generar desde la semana anterior",
  "恢复草稿": "Restaurar borrador",
  "更多": "Más",
  "更多工具": "Más herramientas",
  "设为还原点": "Establecer punto de restauración",
  "还原到初始状态": "Restaurar estado inicial",
  "删除当前草稿": "Eliminar borrador actual",
  "草稿": "Borrador",
  "已归档": "Archivado",
  "数据导入": "Importación de datos",
  "导入本批次数据": "Importar datos a este lote",
  "周报 Excel": "Excel semanal",
  "新增导入": "Importar nuevos",
  "合并更新": "Combinar actualizaciones",
  "钉钉导出表": "Exportación de DingTalk",
  "识别": "Reconocer",
  "从中间表拉取": "Importar desde tabla intermedia",
  "同步钉钉流程": "Sincronizar flujo de DingTalk",
  "员工部门表": "Tabla de departamentos de empleados",
  "按2级部门归组": "Agrupar por departamento de nivel 2",
  "按员工表的2级部门重新归组当前批次": "Reagrupar el lote actual según el departamento de nivel 2 de la tabla de empleados",
  "撤回最近导入": "Deshacer última importación",
  "搜索单号、申请人、摘要、收款方、项目": "Buscar número, solicitante, concepto, beneficiario o proyecto",
  "付款账户": "Cuenta de pago",
  "开票情况": "Estado de factura",
  "待付金额": "Importe pendiente",
  "待付金额（折合人民币）": "Importe pendiente (equivalente en CNY)",
  "待付金额（折合人民币）区间": "Rango pendiente (equivalente en CNY)",
  "最低": "Mínimo",
  "最低待付金额": "Importe pendiente mínimo",
  "最高": "Máximo",
  "最高待付金额": "Importe pendiente máximo",
  "全部财务审批": "Todos los estados financieros",
  "全部总经理审批": "Todas las aprobaciones de dirección",
  "筛选": "Filtrar",
  "正常记录": "Registros activos",
  "已终止/已拒绝": "Terminados / rechazados",
  "全部记录": "Todos los registros",
  "钉钉流程范围": "Alcance del flujo de DingTalk",
  "正常流程": "Flujos activos",
  "全部流程": "Todos los flujos",
  "选择是否查看已终止或已拒绝的钉钉流程": "Seleccione si desea ver flujos de DingTalk terminados o rechazados",
  "导出全部": "Exportar todo",
  "导出筛选结果": "Exportar resultados filtrados",
  "新增": "Nuevo",
  "插入空行": "Insertar fila vacía",
  "换行": "Ajustar texto",
  "取消换行": "No ajustar texto",
  "列设置": "Configurar columnas",
  "保存更改": "Guardar cambios",
  "放弃未保存修改": "Descartar cambios no guardados",
  "当前筛选": "Filtro actual",
  "当前筛选结果": "Resultados filtrados",
  "折合人民币应付": "Total a pagar equivalente en CNY",
  "各币种小计": "Subtotales por moneda",
  "应付": "A pagar",
  "已付": "Pagado",
  "待付": "Pendiente",
  "已付款": "Pagado",
  "部分付款": "Pago parcial",
  "未付款": "No pagado",
  "待我审批": "Pendientes de mi aprobación",
  "全部": "Todos",
  "当前 Sheet": "Sheet actual",
  "全部 Sheet": "Todas las Sheets",
  "Sheet 分页": "Pestañas de Sheet",
  "Sheet 名称": "Nombre de Sheet",
  "向左滚动列": "Desplazar columnas a la izquierda",
  "向右滚动列": "Desplazar columnas a la derecha",
  "删除当前 Sheet": "Eliminar Sheet actual",
  "撤回删除": "Deshacer eliminación",
  "删除所选行": "Eliminar filas seleccionadas",
  "移动所选行": "Mover filas seleccionadas",
  "编辑请款": "Editar solicitud",
  "新增请款": "Nueva solicitud",
  "请款信息": "Información de solicitud",
  "审批信息": "Información de aprobación",
  "付款明细": "Detalle de pagos",
  "钉钉流程": "Flujo de DingTalk",
  "附件": "Adjuntos",
  "基本信息": "Información básica",
  "金额与收款": "Importes y beneficiario",
  "保存请款": "Guardar solicitud",
  "放弃修改": "Descartar cambios",
  "已保存": "Guardado",
  "有未保存修改": "Hay cambios sin guardar",
  "钉钉申请单号": "Número de solicitud en DingTalk",
  "申请人": "Solicitante",
  "应付款公司": "Empresa a pagar",
  "账户性质": "Tipo de cuenta",
  "支出性质": "Naturaleza del gasto",
  "支出类别": "Categoría del gasto",
  "摘要": "Resumen / Concepto",
  "应付金额": "Monto a pagar",
  "已支付金额": "Monto pagado",
  "货币类型": "Tipo de moneda",
  "项目归属": "Proyecto al que pertenece",
  "收款人": "Nombre del beneficiario",
  "收款账号": "Cuenta del beneficiario",
  "收款行": "Banco / Sucursal del beneficiario",
  "是否开具发票": "¿Factura emitida?",
  "需求付款日期": "Fecha de pago requerida",
  "备注": "Observaciones",
  "逾期情况": "Estado de vencimiento",
  "财务审批": "Revisión financiera",
  "财务付款时间": "Fecha real de pago",
  "总经理审批": "Aprobación de dirección general",
  "总经理审批时间": "Fecha de aprobación de dirección",
  "总经理意见": "Opinión de dirección general",
  "未选择": "Sin seleccionar",
  "同意付款": "Aprobar pago",
  "延缓批付": "Posponer pago",
  "存在争议": "En disputa",
  "无需审批": "No requiere aprobación",
  "已完成": "Completado",
  "审批中": "En aprobación",
  "已终止": "Terminado",
  "已拒绝": "Rechazado",
  "未匹配": "Sin coincidencia",
  "来源冲突": "Conflicto de origen",
  "未知": "Desconocido",
  "新增付款": "Nuevo pago",
  "录入第一笔付款": "Registrar el primer pago",
  "本次金额": "Importe de este pago",
  "付款日期": "Fecha de pago",
  "付款人": "Pagador",
  "银行流水号": "Referencia bancaria",
  "付款凭证": "Comprobantes de pago",
  "上传图片": "Subir imagen",
  "添加链接": "Añadir enlace",
  "预览": "Vista previa",
  "删除": "Eliminar",
  "上传": "Subir",
  "关闭": "Cerrar",
  "取消": "Cancelar",
  "保存": "Guardar",
  "保存中": "Guardando",
  "上传中": "Subiendo",
  "查询中": "Consultando",
  "同步中": "Sincronizando",
  "导入中": "Importando",
  "归组中": "Reagrupando",
  "确认": "Confirmar",
  "返回编辑": "Volver a editar",
  "保存后继续": "Guardar y continuar",
  "放弃后继续": "Descartar y continuar",
  "批次": "Lote",
  "记录数": "Registros",
  "待付款金额": "Importe pendiente",
  "查看批次": "Ver lote",
  "导出": "Exportar",
  "操作日志": "Registro de operaciones",
  "日志": "Registro",
  "删除草稿": "Eliminar borrador",
  "归档当前批次": "Archivar lote actual",
  "用户": "Usuarios",
  "姓名": "Nombre",
  "角色": "Rol",
  "Sheet 权限": "Permisos de Sheet",
  "启用": "Activo",
  "操作": "Acciones",
  "新增用户": "Nuevo usuario",
  "创建用户": "Crear usuario",
  "重置密码": "Restablecer contraseña",
  "停用": "Desactivar",
  "当前密码": "Contraseña actual",
  "新密码": "Nueva contraseña",
  "确认新密码": "Confirmar nueva contraseña",
  "确认修改": "Confirmar cambio",
  "修改中": "Actualizando",
  "创建": "Crear",
  "创建中": "Creando",
  "恢复默认": "Restablecer valores predeterminados",
  "上移": "Subir",
  "下移": "Bajar",
  "固定：付款状态": "Fija: Estado de pago",
  "固定：附件": "Fija: Adjuntos",
  "付款状态固定在左侧，附件固定在右侧；其余列可拖拽排序或隐藏。": "El estado de pago queda fijo a la izquierda y los adjuntos a la derecha. Arrastre las demás columnas para ordenarlas.",
  "确认币种处理": "Confirmar cambio de moneda",
  "按汇率换算": "Convertir por tipo de cambio",
  "金额不变，仅更正币种": "Corregir moneda sin cambiar el importe",
  "汇率日期": "Fecha del tipo de cambio",
  "处理前": "Antes",
  "处理后": "Después",
  "确认处理并保存": "Confirmar y guardar",
  "处理中": "Procesando",
  "币种处理方式": "Modo de cambio",
  "人民币基准价值不变，金额按汇率换算。": "El valor equivalente en CNY no cambia.",
  "例如 25,000 CNY 更正为 25,000 USD，人民币折算值随之变化。": "25.000 CNY pasa a 25.000 USD; cambia el equivalente en CNY.",
  "人民币兑本币汇率": "Tipo de cambio frente al CNY",
  "原币汇率": "Tipo de cambio de la moneda original",
  "目标币汇率": "Tipo de cambio de la moneda de destino",
  "实际汇率日期": "Fecha efectiva del tipo de cambio",
  "实际命中日期": "Fecha efectiva",
  "正在读取汇率…": "Consultando el tipo de cambio…",
  "（使用此前最近汇率）": "(se usa el tipo de cambio anterior más reciente)",
  "CNY 人民币": "CNY Yuan chino",
  "USD 美元": "USD Dólar estadounidense",
  "MXN 墨西哥比索": "MXN Peso mexicano",
  "公户": "Cuenta corporativa",
  "私户": "Cuenta personal",
  "无票": "Sin factura",
  "有票": "Con factura",
  "来源 Sheet": "Sheet de origen",
  "BU 归属": "Unidad de negocio",
  "BU归属": "Unidad de negocio",
  "负责人确认": "Confirmación del responsable",
  "财务主管审批": "Aprobación del responsable financiero",
  "付款状态": "Estado de pago",
  "查看 / 审批": "Ver / aprobar",
  "查看详情": "Ver detalles",
  "手机显示模式": "Vista móvil",
  "快捷筛选": "Filtros rápidos",
  "卡片": "Tarjetas",
  "完整表格": "Tabla completa",
  "关闭筛选": "Cerrar filtros",
  "批次已创建": "Lote creado",
  "批次已归档": "Lote archivado",
  "批次已恢复为草稿": "Lote restaurado como borrador",
  "草稿批次已删除": "Borrador eliminado",
  "当前草稿状态已设为还原点": "El estado actual del borrador se guardó como punto de restauración",
  "草稿已还原到初始状态": "El borrador se restauró al estado inicial",
  "Excel 合并更新预览": "Vista previa de combinación de Excel",
  "Excel 明细导入": "Importación detallada de Excel",
  "Excel 汇总导入": "Importación resumida de Excel",
  "合并系统导出后人工维护的 Excel": "Combinar un Excel exportado por el sistema y editado manualmente",
  "付款变化": "Cambios de pago",
  "无变化": "Sin cambios",
  "冲突": "Conflictos",
  "有警告": "Con advertencias",
  "警告": "Advertencia",
  "更新": "Actualizar",
  "跳过": "Omitir",
  "匹配": "Coincidencias",
  "可导入": "Importable",
  "不可导入": "No importable",
  "查询": "Consultar",
  "确认合并": "Confirmar combinación",
  "合并中": "Combinando",
  "钉钉字段映射": "Mapeo de campos de DingTalk",
  "请确认钉钉字段映射": "Confirme el mapeo de campos de DingTalk",
  "按映射导入": "Importar con el mapeo",
  "自动付款候选": "Candidatos de pago automático",
  "付款待核对": "Pago pendiente de revisión",
  "未保存修改已放弃": "Se descartaron los cambios sin guardar",
  "表格更改已保存": "Cambios de la tabla guardados",
  "筛选已应用": "Filtros aplicados",
  "没有符合当前筛选条件的请款。": "No hay solicitudes que coincidan con los filtros actuales.",
  "当前筛选没有可导出的记录": "No hay registros filtrados para exportar",
  "新增 Sheet": "Nueva Sheet",
  "新增 Sheet 名称": "Nombre de la nueva Sheet",
  "新Sheet": "Nueva Sheet",
  "双击重命名": "Doble clic para cambiar el nombre",
  "拖拽调整顺序，双击重命名": "Arrastre para ordenar y haga doble clic para cambiar el nombre",
  "选择目标 Sheet": "Seleccionar Sheet de destino",
  "该 Sheet 当前没有请款记录，将直接移除 Sheet。": "Esta Sheet no contiene solicitudes y se eliminará directamente.",
  "保存并关闭": "Guardar y cerrar",
  "放弃并关闭": "Descartar y cerrar",
  "保存并切换": "Guardar y cambiar",
  "放弃并切换": "Descartar y cambiar",
  "保存请款后继续留在当前抽屉": "Después de guardar, mantenga abierta la solicitud actual",
  "付款、流程和附件单独同步生效": "Los pagos, el flujo y los adjuntos se sincronizan por separado",
  "保存付款修改": "Guardar cambios del pago",
  "编辑付款": "Editar pago",
  "手工录入": "Captura manual",
  "本周录入": "Capturado esta semana",
  "结转继承": "Heredado del periodo anterior",
  "历史迁移": "Migración histórica",
  "日期未知": "Fecha desconocida",
  "付款人未填写": "Pagador no especificado",
  "图片预览": "Vista previa de imagen",
  "上一张图片": "Imagen anterior",
  "下一张图片": "Imagen siguiente",
  "下载": "Descargar",
  "流程链接或本地路径": "Enlace del proceso o ruta local",
  "历史批次": "Lotes históricos",
  "历史快照": "Instantánea histórica",
  "用户已创建": "Usuario creado",
  "用户已保存": "Usuario guardado",
  "用户已保存，密码已修改": "Usuario guardado y contraseña actualizada",
  "用户已启用": "Usuario activado",
  "用户已停用": "Usuario desactivado",
  "用户已删除": "Usuario eliminado",
  "历史币种恢复": "Restauración de moneda histórica",
  "恢复中": "Restaurando",
  "折合人民币金额": "Importe equivalente en CNY",
  "请选择申请开始和结束日期": "Seleccione las fechas inicial y final de la solicitud",
  "申请结束日期不能早于开始日期": "La fecha final no puede ser anterior a la fecha inicial",
  "申请日期格式无效": "El formato de la fecha de solicitud no es válido",
  "请至少选择一个支出来源": "Seleccione al menos un origen de gasto",
  "精确匹配，忽略日期": "Coincidencia exacta; se ignoran las fechas",
  "搜索并多选申请人": "Buscar y seleccionar varios solicitantes",
  "无钉钉号": "Sin número de DingTalk",
  "未识别人员": "Persona no identificada",
  "部门未知": "Departamento desconocido",
  "时间未知": "Hora desconocida",
  "当前节点": "Nodo actual",
  "查看审批": "Ver aprobación",
  "待审批节点": "Etapa pendiente",
  "待识别待办人": "Responsable por identificar",
  "加载此单附件": "Cargar archivos de esta solicitud",
  "附件已完成": "Archivos completos",
  "部分附件失败，可重试": "Algunos archivos fallaron; puede reintentar",
  "附件加载中": "Cargando archivos",
  "附件已排队": "Archivos en cola",
  "附件尚未加载": "Archivos aún no cargados",
  "流程操作": "Operación del flujo",
  "处理": "Procesado",
  "同意": "Aprobado",
  "拒绝": "Rechazado",
};

const esToZh = Object.fromEntries(Object.entries(zhToEs).map(([zh, es]) => [es, zh]));

function translateExact(value: string, language: Language) {
  const trimmed = value.trim();
  if (!trimmed) return value;
  const dictionary = language === "es" ? zhToEs : esToZh;
  const translated = dictionary[trimmed];
  if (translated) return value.replace(trimmed, translated);
  if (language === "es") {
    return value
      .replace(/共\s*(\d+)\s*个用户，在列表中维护已有账号/g, "$1 usuario(s); administre las cuentas existentes en la lista")
      .replace(/已授权\s*(\d+)\s*个\s*Sheet/g, "$1 Sheet(s) autorizada(s)")
      .replace(/(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})/g, "$1 a $2")
      .replace(/CNY：应付/g, "CNY: a pagar")
      .replace(/USD：应付/g, "USD: a pagar")
      .replace(/MXN：应付/g, "MXN: a pagar")
      .replace(/· 已付/g, "· pagado")
      .replace(/· 待付/g, "· pendiente")
      .replace(/\/ 待付/g, "/ pendiente")
      .replace(/(\d+)\s*条/g, "$1 registro(s)")
      .replace(/(\d+)\s*单/g, "$1 solicitud(es)")
      .replace(/(\d+)\s*笔/g, "$1 pago(s)")
      .replace(/已选\s*(\d+)\s*条/g, "$1 seleccionado(s)")
      .replace(/附件\s*(\d+)/g, "$1 adjunto(s)")
      .replace(/付款\s*(\d+)\s*笔/g, "$1 pago(s)");
  }
  return value
    .replace(/(\d+) registro\(s\)/g, "$1 条")
    .replace(/(\d+) solicitud\(es\)/g, "$1 单")
    .replace(/(\d+) pago\(s\)/g, "$1 笔")
    .replace(/(\d+) seleccionado\(s\)/g, "已选 $1 条")
    .replace(/(\d+) adjunto\(s\)/g, "附件 $1");
}

function translateNode(node: Node, language: Language) {
  if (node.nodeType === Node.TEXT_NODE && node.nodeValue) {
    const next = translateExact(node.nodeValue, language);
    if (next !== node.nodeValue) node.nodeValue = next;
    return;
  }
  if (!(node instanceof HTMLElement)) return;
  ["placeholder", "title", "aria-label"].forEach((attribute) => {
    const current = node.getAttribute(attribute);
    if (!current) return;
    const next = translateExact(current, language);
    if (next !== current) node.setAttribute(attribute, next);
  });
  node.childNodes.forEach((child) => translateNode(child, language));
}

type LanguageContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  toggleLanguage: () => void;
  t: (zh: string, es?: string) => string;
};

const LanguageContext = createContext<LanguageContextValue | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => {
    const saved = window.localStorage.getItem(storageKey) || window.localStorage.getItem("payment-grid-header-language");
    return saved === "es" ? "es" : "zh";
  });

  function setLanguage(next: Language) {
    window.localStorage.setItem(storageKey, next);
    window.localStorage.setItem("payment-grid-header-language", next);
    document.documentElement.lang = next === "es" ? "es" : "zh-CN";
    setLanguageState(next);
  }

  useEffect(() => {
    setLanguage(language);
    const root = document.getElementById("root");
    if (!root) return;
    translateNode(root, language);
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === "characterData") translateNode(mutation.target, language);
        mutation.addedNodes.forEach((node) => translateNode(node, language));
      });
    });
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [language]);

  const value = useMemo<LanguageContextValue>(() => ({
    language,
    setLanguage,
    toggleLanguage: () => setLanguage(language === "zh" ? "es" : "zh"),
    t: (zh: string, es?: string) => language === "es" ? (es || zhToEs[zh] || zh) : zh,
  }), [language]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const value = useContext(LanguageContext);
  if (!value) throw new Error("useLanguage must be used inside LanguageProvider");
  return value;
}

export function currentLanguage(): Language {
  return window.localStorage.getItem(storageKey) === "es" ? "es" : "zh";
}

export function translateKnownError(message: string) {
  if (currentLanguage() !== "es") return message;
  return translateExact(message, "es") === message ? `No se pudo completar la operación: ${message}` : translateExact(message, "es");
}
