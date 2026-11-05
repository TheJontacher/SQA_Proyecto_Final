# Matriz de pruebas automatizadas

## Criterio y evidencia

La matriz contiene los 91 métodos `test_*` encontrados en las tres suites `unittest`. El estado PASS se refiere a la última ejecución verde de GitHub Actions en `main`, run [41961032413](https://github.com/TheJontacher/SQA_Proyecto_Final/actions/runs/41961032413), donde TEST reportó 29, 44 y 18 pruebas y verificó el total de 91. El éxito de cada suite implica que sus métodos terminaron sin fallo en esa ejecución; no se atribuye al run un reporte individual por método que no se conserva.

Las bases son SQLite temporales y los datos de prueba son ficticios. Las interacciones externas se simulan. En las tablas, “esperado” resume el comportamiento comprobado por el método, no reproduce datos ni textos clínicos.

**Tipos:** `Int.` = integración; `Reg.` = regresión; `Unit.` = comprobación unitaria acotada. Muchas pruebas combinan más de un tipo.

## Autenticación, seguridad, configuración de correo y 2FA, IA, FEL y pacientes

Suite: [`tests/test_2fa_y_ia.py`](../tests/test_2fa_y_ia.py) — 29 métodos.

| ID | Módulo funcional | Método de prueba | Tipo | Objetivo, escenario y resultado esperado | Resultado |
|---|---|---|---|---|---|
| T2-01 | Correo / secretos | `test_01_las_claves_se_guardan_cifradas` | Int./Reg. | Guardar configuración sensible de correo cifrada, no en texto claro. | PASS |
| T2-02 | IA / configuración | `test_02_modelo_invalido_rechazado` | Int./Reg. | Rechazar un identificador de modelo inválido. | PASS |
| T2-03 | Correo | `test_03_correo_de_prueba` | Int. | Probar configuración de correo usando transporte simulado; resultado controlado por la prueba. | PASS |
| T2-04 | 2FA | `test_03b_sin_probar_no_se_exige_el_codigo` | Int./Reg. | Si 2FA no está activado mediante prueba de correo, no exigir código. | PASS |
| T2-05 | Autorización / IA | `test_04_solo_admin_entra_a_configuracion_e_ia` | Int./Seg. | Permitir configuración/IA a administrador y denegar al usuario normal. | PASS |
| T2-06 | Usuarios | `test_10_crear_usuario_exige_correo_valido` | Int./Reg. | Rechazar creación de usuario con correo no válido. | PASS |
| T2-07 | Usuarios | `test_11_correo_guardado_en_minusculas_y_no_se_repite` | Int./Reg. | Normalizar correo a minúsculas y evitar duplicados. | PASS |
| T2-08 | Autenticación / 2FA | `test_12_login_pide_codigo_y_no_abre_sesion_antes` | Int./Seg. | Tras contraseña correcta, solicitar código y no autenticar antes de validarlo. | PASS |
| T2-09 | 2FA | `test_13_el_codigo_se_usa_una_sola_vez` | Int./Reg. | Aceptar el código una vez y rechazar su reutilización. | PASS |
| T2-10 | 2FA | `test_14_cinco_fallos_invalidan_el_codigo` | Int./Seg. | Invalidar el código luego del límite de intentos probado. | PASS |
| T2-11 | 2FA | `test_15_codigo_vencido` | Int./Reg. | Rechazar código expirado. | PASS |
| T2-12 | 2FA / datos | `test_16_el_codigo_no_se_guarda_en_claro` | Int./Seg. | Persistir verificador/hash, no el código en texto claro. | PASS |
| T2-13 | Autenticación | `test_17_contrasena_incorrecta_no_envia_codigo` | Int./Seg. | No iniciar flujo de envío de código ante contraseña incorrecta. | PASS |
| T2-14 | 2FA | `test_18_reenvio_respeta_la_espera` | Int./Reg. | Aplicar el intervalo mínimo de reenvío. | PASS |
| T2-15 | Autenticación / correo | `test_19_si_falla_el_envio_no_hay_acceso` | Int./Seg. | No completar autenticación cuando el envío requerido falla. | PASS |
| T2-16 | Usuarios / 2FA | `test_20_usuario_sin_correo_entra_y_debe_registrarlo` | Int./Reg. | Permitir el flujo de registro y confirmación de correo para usuario legado sin correo. | PASS |
| T2-17 | IA / privacidad | `test_30_el_reporte_manda_cifras_y_nunca_datos_de_pacientes` | Int./Seg. | Enviar métricas agregadas y excluir nombres/datos de pacientes del prompt. | PASS |
| T2-18 | IA / correo | `test_31_enviar_por_correo` | Int. | Enviar reporte mediante transporte de correo simulado. | PASS |
| T2-19 | IA / validación | `test_32_destinatario_invalido_no_llama_a_la_ia` | Int./Reg. | Rechazar destinatario no válido antes de invocar IA. | PASS |
| T2-20 | IA / errores | `test_33_error_de_openai_se_explica_y_no_guarda` | Int./Reg. | Manejar error del proveedor simulado y no guardar una respuesta inválida. | PASS |
| T2-21 | IA / salida | `test_34_el_markdown_se_escapa` | Int./Seg. | Escapar contenido Markdown/HTML antes de mostrarlo. | PASS |
| T2-22 | IA / auditoría | `test_35_el_token_no_queda_en_la_auditoria` | Int./Seg. | Evitar registrar el token de IA en auditoría. | PASS |
| T2-23 | FEL / impuestos | `test_40_pequeno_contribuyente_sin_iva_y_como_FPEQ` | Int./Reg. | Calcular modalidad de pequeño contribuyente sin IVA y tipo FPEQ. | PASS |
| T2-24 | FEL / impuestos | `test_41_regimen_general_no_cambia` | Int./Reg. | Preservar el cálculo del régimen general. | PASS |
| T2-25 | FEL / descuentos | `test_42_con_descuento_el_total_cuadra` | Int./Reg. | Mantener consistencia entre descuento y total de factura. | PASS |
| T2-26 | FEL / interfaz | `test_43_se_guarda_desde_la_pantalla` | Int. | Persistir la configuración fiscal enviada desde la pantalla. | PASS |
| T2-27 | Pacientes / SAT | `test_50_separar_nombre_de_sat` | Int./Reg. | Separar nombre y apellidos del resultado de consulta fiscal. | PASS |
| T2-28 | Pacientes / SAT | `test_51_la_consulta_devuelve_nombres_y_apellidos` | Int. | Devolver por separado nombre y apellidos de la consulta. | PASS |
| T2-29 | Pacientes / formulario | `test_52_el_formulario_de_paciente_llena_ambas_casillas` | Int. | Completar los campos de nombres y apellidos del formulario. | PASS |

## Inventario, ventas, caja, finanzas, facturación y seguridad

Suite: [`tests/test_dinero_y_facturacion.py`](../tests/test_dinero_y_facturacion.py) — 44 métodos.

| ID | Módulo funcional | Método de prueba | Tipo | Objetivo, escenario y resultado esperado | Resultado |
|---|---|---|---|---|---|
| DF-01 | Migración / datos | `test_conserva_los_datos_y_sus_totales` | Int./Reg. | Migrar el esquema conservando registros y totales previos. | PASS |
| DF-02 | Migración / CxC | `test_creditos_pasan_a_cuentas_por_cobrar` | Int./Reg. | Trasladar créditos existentes a cuentas por cobrar con sus saldos. | PASS |
| DF-03 | Migración / CxP | `test_facturas_de_proveedor_pasan_a_cuentas_por_pagar` | Int./Reg. | Trasladar obligaciones de proveedor a cuentas por pagar. | PASS |
| DF-04 | Migración / caja | `test_el_efectivo_del_turno_abierto_se_conserva` | Int./Reg. | Conservar el efectivo asociado al turno abierto. | PASS |
| DF-05 | Migración | `test_es_idempotente` | Int./Reg. | Repetir inicialización/migración sin duplicar efectos. | PASS |
| DF-06 | Inicialización | `test_base_nueva_se_crea_sin_perder_ni_borrar` | Int./Reg. | Crear esquema nuevo sin borrar datos existentes. | PASS |
| DF-07 | Rutas de dinero | `test_01_todas_las_pantallas_abren` | Int. | Verificar respuesta de las pantallas funcionales del módulo. | PASS |
| DF-08 | Compatibilidad de rutas | `test_02_las_direcciones_antiguas_siguen_funcionando` | Int./Reg. | Mantener compatibilidad de las rutas históricas cubiertas. | PASS |
| DF-09 | Caja / fondo | `test_10_cerrar_el_turno_antiguo_manda_el_efectivo_al_fondo` | Int./Reg. | Transferir al fondo el efectivo del cierre según el flujo probado. | PASS |
| DF-10 | Ventas / caja | `test_11_no_se_vende_sin_caja_abierta` | Int./Seg. | Bloquear venta en efectivo si no hay caja abierta. | PASS |
| DF-11 | Caja | `test_12_abrir_caja_exige_origen_y_saldo` | Int./Reg. | Exigir origen y saldo inicial al abrir turno. | PASS |
| DF-12 | Ventas | `test_20_venta_en_efectivo` | Int. | Registrar venta en efectivo y sus efectos esperados. | PASS |
| DF-13 | Ventas | `test_21_el_doble_clic_no_duplica_la_venta` | Int./Reg. | Evitar duplicar una venta ante envío repetido. | PASS |
| DF-14 | Ventas / pagos | `test_22_ventas_con_tarjeta_y_transferencia_van_a_su_cuenta` | Int. | Registrar tarjeta y transferencia en las cuentas correspondientes. | PASS |
| DF-15 | Ventas / validación | `test_23_sin_referencia_no_se_vende_con_tarjeta` | Int./Reg. | Exigir referencia para el pago con tarjeta probado. | PASS |
| DF-16 | Ventas / validación | `test_24_validaciones_de_la_venta` | Int./Reg. | Rechazar combinaciones de entrada inválidas del escenario. | PASS |
| DF-17 | CxC | `test_25_venta_a_credito_crea_la_cuenta` | Int. | Crear cuenta por cobrar al registrar venta a crédito. | PASS |
| DF-18 | CxC | `test_26_cobrar_la_cuenta` | Int. | Registrar abono y actualizar saldo de la cuenta. | PASS |
| DF-19 | CxC / caja | `test_27_cobrar_en_efectivo_exige_caja_abierta_y_las_cuentas_antiguas_siguen_cobrables` | Int./Reg. | Exigir caja abierta para efectivo y mantener cobrables las cuentas legadas. | PASS |
| DF-20 | Gastos | `test_30_gastos` | Int. | Registrar gasto por la ruta funcional con las validaciones cubiertas. | PASS |
| DF-21 | Gastos / caja | `test_31_anular_un_gasto_devuelve_el_dinero` | Int./Reg. | Anular gasto y revertir el efecto monetario esperado. | PASS |
| DF-22 | CxP | `test_32_pagar_una_cuenta_por_pagar` | Int. | Registrar pago válido de cuenta por pagar. | PASS |
| DF-23 | Conciliación / tarjetas | `test_33_consolidar_tarjeta` | Int. | Trasladar el saldo de tarjeta por consolidar al fondo. | PASS |
| DF-24 | Ventas / inventario | `test_40_cancelar_una_venta_en_efectivo_devuelve_stock_y_dinero` | Int./Reg. | Revertir stock y dinero al cancelar la venta permitida. | PASS |
| DF-25 | Ventas / CxC | `test_41_una_venta_a_credito_con_abonos_no_se_cancela` | Int./Seg. | Bloquear cancelación de venta a crédito con abonos. | PASS |
| DF-26 | Ventas / legado | `test_42_una_venta_antigua_se_cancela_sin_mover_dinero` | Int./Reg. | Cancelar venta histórica sin mover dinero del periodo actual. | PASS |
| DF-27 | Permisos | `test_43_el_usuario_normal_no_puede_cancelar` | Int./Seg. | Denegar cancelación a usuario sin privilegio administrativo. | PASS |
| DF-28 | Autorización | `test_50_permisos_del_usuario_normal` | Int./Seg. | Aplicar permisos de usuario normal a las rutas comprobadas. | PASS |
| DF-29 | Autenticación | `test_51_hay_que_iniciar_sesion` | Int./Seg. | Requerir sesión para rutas protegidas. | PASS |
| DF-30 | CSRF | `test_52_csrf` | Int./Seg. | Rechazar solicitudes mutables sin token CSRF válido. | PASS |
| DF-31 | Autenticación | `test_53_bloqueo_por_intentos_fallidos` | Int./Seg. | Aplicar bloqueo tras intentos fallidos del escenario. | PASS |
| DF-32 | Contraseña | `test_53b_aviso_de_contrasena_predeterminada` | Int./Reg. | Mostrar el aviso configurado cuando se detecta la condición de contraseña predeterminada. | PASS |
| DF-33 | Redirección | `test_54_redireccion_abierta_bloqueada` | Int./Seg. | Rechazar el destino externo no permitido del flujo de login. | PASS |
| DF-34 | Pacientes / papelera | `test_55_no_se_borra_para_siempre_un_paciente_con_historial` | Int./Reg. | Preservar paciente referido por historial clínico/operativo. | PASS |
| DF-35 | Archivos / seguridad | `test_56_subida_de_archivos_peligrosos` | Int./Seg. | Rechazar archivos fuera de la lista permitida. | PASS |
| DF-36 | Pacientes / facturación | `test_57_nit_del_paciente` | Int./Reg. | Validar y conservar el NIT del paciente conforme al escenario. | PASS |
| DF-37 | Caja / conciliación | `test_60_la_conciliacion_cuadra_tras_todo_lo_anterior` | Int./Reg. | Conciliar operaciones del flujo y exigir que no haya diferencias inexplicadas. | PASS |
| DF-38 | Finanzas | `test_61_estado_de_resultados` | Int. | Calcular el estado de resultados con los datos ficticios del caso. | PASS |
| DF-39 | Reportes financieros | `test_62_los_reportes_aceptan_filtros_raros` | Int./Reg. | Manejar filtros extremos/inusuales sin error ni consulta inválida. | PASS |
| DF-40 | Exportaciones | `test_63_exportaciones` | Int. | Generar los formatos de exportación que cubre la prueba. | PASS |
| DF-41 | Facturación FEL | `test_70_facturacion_electronica` | Int. | Emitir la solicitud FEL usando respuesta simulada del proveedor. | PASS |
| DF-42 | Facturación FEL | `test_71_factura_a_consumidor_final_con_servicio_y_descuentos` | Int./Reg. | Construir factura CF con servicio y descuento distribuidos correctamente. | PASS |
| DF-43 | Facturación FEL / red | `test_72_falla_de_red_deja_la_factura_indeterminada` | Int./Reg. | No reintentar a ciegas; conservar estado indeterminado tras fallo simulado. | PASS |
| DF-44 | Auditoría | `test_80_la_auditoria_registra_lo_importante` | Int./Seg. | Registrar eventos relevantes sin exponer los secretos probados. | PASS |

## Pacientes, expedientes, citas, inventario, caja, migraciones y regresiones

Suite: [`tests/test_regressions.py`](../tests/test_regressions.py) — 18 métodos.

| ID | Módulo funcional | Método de prueba | Tipo | Objetivo, escenario y resultado esperado | Resultado |
|---|---|---|---|---|---|
| RG-01 | Pacientes / expedientes / archivos | `test_patient_expedient_session_and_protected_document_flow` | Int./Reg. | Cubrir sesión, expediente y acceso protegido a documento con usuario autenticado. | PASS |
| RG-02 | Caja | `test_cash_open_close_is_validated_and_idempotent` | Int./Reg. | Validar apertura/cierre e impedir repetir la operación sin efectos duplicados. | PASS |
| RG-03 | Finanzas / reportes | `test_service_profit_report_treats_null_purchase_cost_as_zero` | Int./Reg. | Tratar costo de compra NULL como cero en reporte de utilidad. | PASS |
| RG-04 | Base de datos | `test_database_initializer_is_repeatable_and_preserves_data` | Int./Reg. | Repetir inicializador y conservar datos de la base temporal. | PASS |
| RG-05 | CSRF / autenticación | `test_post_requests_require_csrf_and_logout_is_post_only` | Int./Seg. | GET logout devuelve 405; POST inválido no cierra sesión; POST con CSRF válido sí la cierra. | PASS |
| RG-06 | Fechas / utilidades | `test_current_date_helpers_parse_and_format_expected_inputs` | Unit./Reg. | Parsear y formatear fechas en los casos incluidos por la prueba. | PASS |
| RG-07 | Ventas / inventario / caja | `test_sale_stock_and_cancellation_are_consistent` | Int./Reg. | Mantener consistencia entre venta, existencias y cancelación. | PASS |
| RG-08 | CxC | `test_credit_installments_cannot_overpay` | Int./Reg. | Rechazar abonos que exceden el saldo de una cuenta por cobrar. | PASS |
| RG-09 | CxP / gastos | `test_expense_installments_cannot_overpay` | Int./Reg. | Rechazar abonos que exceden el saldo de una obligación. | PASS |
| RG-10 | Citas | `test_appointment_edit_rejects_occupied_time` | Int./Reg. | Rechazar edición que ocupa un horario ya reservado. | PASS |
| RG-11 | Citas | `test_appointment_creation_accepts_free_slot_and_rejects_conflict` | Int./Reg. | Aceptar horario libre y rechazar conflicto de agenda. | PASS |
| RG-12 | Inventario | `test_stock_adjustment_cannot_make_negative_or_change_service_stock` | Int./Reg. | Evitar inventario negativo y cambios de stock en servicios. | PASS |
| RG-13 | Papelera / historial | `test_trash_permanent_delete_preserves_referenced_clinical_and_stock_history` | Int./Reg. | Impedir borrado permanente cuando hay referencias históricas protegidas. | PASS |
| RG-14 | Archivos / validación | `test_upload_allowlist` | Unit./Seg. | Aceptar extensiones permitidas y rechazar extensiones no incluidas. | PASS |
| RG-15 | Migración PostgreSQL | `test_migration_schema_preserves_username_unique_and_date_type` | Unit./Reg. | Validar unicidad del usuario y tipo de fecha en el esquema migrado, sin servidor real. | PASS |
| RG-16 | Migración PostgreSQL | `test_sequence_repair_only_advances_when_behind` | Unit./Reg. | Avanzar secuencia solo cuando está retrasada respecto a datos. | PASS |
| RG-17 | Migración PostgreSQL | `test_sequence_sync_command_advances_only_the_stale_sequence` | Unit./Reg. | Sincronizar únicamente la secuencia obsoleta del escenario. | PASS |
| RG-18 | Migración PostgreSQL / seguridad | `test_migration_refuses_nonempty_postgres_schema` | Unit./Seg. | Rehusar migración a un esquema destino no vacío, usando dobles de prueba. | PASS |

## Resumen verificable

| Suite | Métodos documentados | Ejecutados en run verde | Fallidos/errores en ese run |
|---|---:|---:|---:|
| `tests.test_2fa_y_ia` | 29 | 29 | 0 |
| `tests.test_dinero_y_facturacion` | 44 | 44 | 0 |
| `tests.test_regressions` | 18 | 18 | 0 |
| **Total** | **91** | **91** | **0** |

La ejecución verde y el conteo del job TEST están enlazados en la introducción. Los estados de esta matriz no deben interpretarse como evidencia de cobertura completa, prueba de producción o validación en un servicio externo real.
