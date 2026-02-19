# Comparativa de Arquitectura: Legacy vs. Solución Python
### Toward a Professional, Scalable, and Sustainable Integration Strategy

Este documento detalla la evolución tecnológica del sistema de procesamiento de **Notas de Crédito (NC)**, contrastando la solución anterior basada en RPA y servicios en la nube frente al nuevo ecosistema unificado en Python.

---

## 1. Visión General de la Evolución

| Característica | Arquitectura Legacy (RPA) | Nueva Arquitectura (Python) |
| :--- | :--- | :--- |
| **Motor de Ejecución** | Rocketbot en Máquina Virtual (VM) | Script nativo Python |
| **Extracción de Datos** | Scrapping vía Google Apps Script | Consumo directo de API REST |
| **Almacenamiento Mapeos** | Google Drive / Google Sheets | Archivos CSV locales (versionables en Git) |
| **Conectividad ERP** | Web Scraping / Post descentralizado | API de Finnegans (OAuth2 / REST) |
| **Control de Versiones** | Ninguno | Git (GitHub) |
| **Costo Infraestructura** | VM mensual + licencias Rocketbot + GCloud | $0 (Python es gratuito y abierto) |
| **Mantenimiento** | Alto (dependiente de UI web y nube) | Bajo (endpoints de API estables) |

---

## 2. Definiciones Clave

> **RPA (Robotic Process Automation):** Es una categoría de software que automatiza procesos imitando las acciones de un usuario humano sobre una interfaz gráfica (haciendo clic en botones, copiando datos entre pantallas, etc.). Herramientas como Rocketbot, UiPath o Power Automate Desktop pertenecen a esta familia. Son efectivas para tareas repetitivas, pero **frágiles ante cambios de la interfaz web o del proveedor**, ya que dependen de la posición exacta de los elementos en pantalla.

> **Web Scraping:** Técnica de extracción de datos de páginas web analizando el HTML o la interfaz visual, en lugar de consumir una API oficial. Es útil cuando no existe una API, pero es considerada una solución de **última instancia** por su fragilidad ante actualizaciones del sitio.

> **API REST (Application Programming Interface):** Es un canal de comunicación oficial, estable y documentado que expone los datos de un sistema de forma estructurada. Comunicarse vía API es el **estándar de la industria** para integraciones entre sistemas empresariales.

---

## 3. Diferencias Clave e Impacto en la Operación

### 3.1. Descentralización vs. Unificación de Servicios

**Antes (Legacy):**
Para que el proceso funcionara debían estar activos y coordinados simultáneamente:
1. La Máquina Virtual en la nube.
2. La licencia y el servicio de Rocketbot.
3. La disponibilidad de Google Cloud (Apps Script).
4. Los permisos de Google Drive para leer los mapeos.
5. El acceso al portal web de la Cooperativa.

Cada componente representaba un punto de falla independiente. Si Google Apps Script sufría una actualización o una cuota excedida, el proceso completo se detenía.

**Ahora (Python):**
Un único punto de entrada (`main.py`) orquesta las tres fases del proceso ─ extracción, transformación y carga ─ sin intermediarios. Si un paso falla, el sistema registra el error y continúa con el documento siguiente, sin interrumpir el lote completo.

---

### 3.2. Robustez y Manejo de Errores

- **Legacy:** Los scripts de Google Apps Script tienen límites de ejecución de entre 6 y 30 minutos y cuotas de red. Si un lote de NCs era demasiado grande, el proceso fallaba a mitad de camino sin posibilidad de retomar desde donde se cortó.
- **Python:** Manejo de excepciones nativo. El sistema utiliza carpetas de estado (`Finnegans_OK`, `Finnegans_Error`) para saber exactamente qué se procesó y qué no. El proceso puede relanzarse sobre los archivos fallidos sin reprocesar los exitosos.

---

### 3.3. Velocidad y Latencia

- **Legacy:** Cada consulta de un mapeo de producto requería una llamada HTTP a la API de Google Sheets. En un lote de 300 ítems, eso implicaba 300 peticiones de red adicionales a servidores externos, sumando hasta 3-5 minutos de latencia de solo lectura.
- **Python:** Los mapeos se cargan completamente en memoria al inicio de la ejecución. La búsqueda es instantánea (microsegundos), independientemente del tamaño del catálogo.

---

### 3.4. Control de Versiones y Auditoría

- **Legacy:** No existía control de versiones. Un cambio en el script de Apps Script sobrescribía el anterior. Ante un error, no había forma de saber qué versión del código estaba vigente o cuándo se introdujo el bug.
- **Python:** El código vive en un repositorio Git (GitHub). Cada cambio queda firmado con fecha, autor y descripción. Es posible revertir a cualquier versión anterior en segundos, comparar diferencias y auditar la historia completa del sistema.

---

## 4. Diagnóstico: El Problema Estructural del No-Code y los Límites del RPA

Esta sección aborda una discusión estratégica que va más allá del sistema de NCs: **¿Debe una empresa en crecimiento apostar por plataformas No-Code o por desarrollo de software asistido por IA?**

### El Atractivo del No-Code (y por qué no es la respuesta para el largo plazo)

Plataformas como **AppSheet**, **Bubble**, **Power Apps** o **Rocketbot** ofrecen una propuesta seductora: _"Cualquier persona puede crear soluciones sin escribir código"_. Y en un contexto acotado, cumplen la promesa. Para un formulario de solicitudes internas o un tablero de seguimiento sencillo, su curva de aprendizaje baja es una ventaja real.

Sin embargo, a medida que el negocio crece, emergen limitaciones críticas:

| Factor | Plataformas No-Code | Código Profesional (Python + Git) |
| :--- | :--- | :--- |
| **Curva de aprendizaje** | Baja inicialmente, pero alta cuando la lógica crece | Media inicial, estable a largo plazo |
| **Dependencia de proveedor** | **Total.** Si el servicio sube precios, cambia su API o cierra, el sistema muere. | **Ninguna.** Python es Open Source y estará disponible para siempre. |
| **Capacidad de auditoría** | Limitada. Los flujos visuales son difíciles de documentar y revisar. | Completa. Cada línea de código es legible, auditable y controlable. |
| **Escalabilidad** | Rígida. Los límites los impone la plataforma (cuotas, tiers). | Ilimitada. Solo los límites del hardware propio. |
| **Seguridad** | Los datos fluyen por servidores del proveedor. Las credenciales se almacenan en plataformas de terceros. | Los datos y credenciales nunca salen del entorno de la empresa. |
| **Costo a escala** | Exponencial. Los planes se encarecen con cada usuario y cada transacción. | Mínimo. Los costos son de infraestructura propia, predecibles. |
| **Control de versiones** | Básico o inexistente (historial de cambios visual, no código). | Estándar. Git ofrece trazabilidad completa y colaboración real. |

### El Problema de la "Trampa del Barro" (*Mud Trap*)

La experiencia con **Rocketbot** en este proyecto es un caso de manual de cómo el No-Code y el RPA pueden convertirse en una trampa:

1. **Fragilidad ante el cambio:** El sistema dependía de la estructura visual del portal web de la Cooperativa. Cualquier rediseño del portal por parte del proveedor podía dejar el proceso inoperativo de un día para otro.
2. **Dificultad de diagnóstico:** Cuando algo fallaba en la VM, identificar el origen del error requería acceder al servidor virtual, revisar logs difusos y, en ocasiones, reproducir el problema manualmente.
3. **Cero portabilidad:** El flujo de Rocketbot solo funciona en Rocketbot. No existe la posibilidad de migrar ese conocimiento a otro sistema.
4. **Costos ocultos:** La VM, la licencia de RPA y los servicios de Google Cloud son costos recurrentes que no desaparecen aunque el negocio no crezca.

### Por Qué el Código Asistido por IA es la Apuesta Ganadora

Una de las objeciones históricas al desarrollo de software en las organizaciones era: _"No tenemos un equipo de programadores"_. Esa barrera ha desaparecido.

Herramientas de **Inteligencia Artificial asistida para código** ─ como **Antigravity** (usada en este proyecto), **GitHub Copilot**, **Windsurf** o **Cursor** ─ han cambiado el paradigma completamente:

- **No es necesario saber programar para dar instrucciones.** En este proyecto, la lógica de negocio (cómo interpretar una NC de tipo 0271 vs 0272, cómo mapear un proveedor, cómo interactuar con Finnegans) fue explicada en lenguaje natural, y la IA la transformó en código funcional.
- **La IA escribe el código; el analista valida la lógica.** Esto invierte la dinámica: el conocimiento del negocio sigue siendo el activo más valioso, y la tecnología se convierte en un traductor de ese conocimiento, no en una barrera.
- **El resultado es propietario y auditable.** A diferencia de un flujo en AppSheet que "vive" en los servidores de Google, el código generado es de la empresa, vive en su repositorio y puede ser entendido, modificado y mantenido por cualquier desarrollador en el futuro.

---

## 5. Conclusión y Recomendación Estratégica

La transición desde Rocketbot + Apps Script hacia una solución Python unificada no fue solo una mejora técnica. Fue una **decisión de madurez organizacional**.

Las plataformas No-Code y RPA tienen su lugar en el ecosistema empresarial, pero ese lugar no puede ser el corazón de los procesos de negocio críticos. Depender de ellas para integrar el ERP con los canales comerciales es como construir los cimientos de un edificio con bloques de madera: funciona hasta que llueve.

**La combinación de código abierto + IA generativa + control de versiones profesional** representa el estándar que las empresas en crecimiento deben adoptar. No porque sea la opción más sencilla a corto plazo, sino porque es la única que ofrece:

- ✅ **Soberanía tecnológica**: El código le pertenece a Don Yeyo, no a un vendor.
- ✅ **Escalabilidad real**: La misma arquitectura puede procesar 10 NCs o 10.000 sin cambiar de plataforma.
- ✅ **Seguridad**: Las credenciales y los datos sensibles nunca abandonan la infraestructura propia.
- ✅ **Trazabilidad**: Cada decisión técnica queda registrada y es reversible.
- ✅ **Costo predecible**: Sin sorpresas de facturación por superar cuotas o tiers de licencia.

> _El No-Code promete velocidad. La IA asistida por código entrega velocidad **y** control. En un entorno empresarial en crecimiento, sacrificar el control por la aparente comodidad es un riesgo que se paga caro: casi siempre más tarde, con más urgencia y con menos opciones sobre la mesa._

**La inversión en este nuevo sistema no fue en infraestructura cara ni en licencias de software. Fue en conocimiento, en arquitectura de datos y en herramientas que la industria global ya adoptó como estándar.** Esa es la diferencia entre una solución que dura un año y una que escala con la empresa.
