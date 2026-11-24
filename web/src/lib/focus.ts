// tiny cross-page hand-off: "open this cluster on the Analytics tab", "open the glossary"
let cluster: string | null = null
export const setClusterFocus = (id: string | null) => { cluster = id }
export const takeClusterFocus = () => { const c = cluster; cluster = null; return c }
export const openGlossary = () => window.dispatchEvent(new Event("ufa:glossary"))
