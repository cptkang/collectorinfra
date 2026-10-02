<%@ page import="java.util.*" contentType="text/plain; charset=UTF-8" %><%
@SuppressWarnings("unchecked")
List<byte[]> held = (List<byte[]>) application.getAttribute("held");
if (held == null) {
    held = Collections.synchronizedList(new ArrayList<byte[]>());
    application.setAttribute("held", held);
}
if (request.getParameter("reset") != null) held.clear();
int mb = 0;
try { mb = Integer.parseInt(request.getParameter("mb")); } catch (Exception ignored) { }
mb = Math.max(0, Math.min(mb, 256));
for (int i = 0; i < mb; i++) held.add(new byte[1024 * 1024]);
out.print("held MB=" + held.size());
%>
