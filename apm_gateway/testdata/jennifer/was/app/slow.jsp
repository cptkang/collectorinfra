<%@ page contentType="text/plain; charset=UTF-8" %><%
long ms = 1000;
try { ms = Long.parseLong(request.getParameter("ms")); } catch (Exception ignored) { }
ms = Math.max(0, Math.min(ms, 60000));
Thread.sleep(ms);
out.print("slept " + ms + "ms");
%>
