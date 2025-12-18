template_str = """
<!DOCTYPE html>
<html>
<head>
    <meta chartset="utf-8"
    <title>{{ title }}</title>
</head>
<body>
    <p>{{ intro_text }}</p>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr>
            <th>Title</th>
            <th>AI Summary</th>
            <th>Company</th>
            <th>Industries</th>
        </tr>
        {% for row in rows %}
            <tr>
                <td>
                    <a href="{{row.content.extracted.article_link}}">
                        {{row.content.extracted.title}}
                    </a>
                </td>
                <td>{{row.content.extracted.summary}}</td>
                <td>{{row.content.extracted.company}}</td>
                <td>{{", ".join(row.content.extracted.industries)}}</td>
            </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

test_template_str = """
<!DOCTYPE html>
<html>
<head>
    <meta chartset="utf-8"
    <title>{{ title }}</title>
</head>
<body>
    <p>{{ intro_text }}</p>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr>
            <th>Title</th>
            <th>AI Summary</th>
            <th>Company</th>
            <th>Industries</th>
        </tr>
        {% for row in rows %}
            <tr>
                <td>
                    <a href="{{row.article_link}}">
                        {{row.title}}
                    </a>
                </td>
                <td>{{row.summary}}</td>
                <td>{{row.company}}</td>
                <td>{{", ".join(row.industries)}}</td>
            </tr>
        {% endfor %}
    </table>
</body>
</html>
"""