# Comparador de Times - Apostar Stats

Este é um front-end simples para comparar estatísticas de times de futebol.

## Como usar

### Método 1: Abrir diretamente (Solução Rápida)
1. Abra o arquivo `index.html` diretamente no seu navegador
2. O sistema carregará os dados do arquivo `load-data.js`
3. Selecione a liga, escolha dois times e clique em "Comparar"

### Método 2: Usando um servidor local (Solução Completa)
Se você quiser carregar o arquivo JSON completo ou fazer alterações frequentes, é recomendável usar um servidor local:

#### Usando Python (Opção mais simples)
1. Abra o terminal na pasta do projeto
2. Execute um dos comandos abaixo:

Para Python 3:
```
python -m http.server
```

Para Python 2:
```
python -m SimpleHTTPServer
```

3. Acesse `http://localhost:8000` no seu navegador

#### Usando Node.js
1. Instale o http-server globalmente (se ainda não tiver):
```
npm install -g http-server
```

2. Execute na pasta do projeto:
```
http-server
```

3. Acesse `http://localhost:8080` no seu navegador

## Atualização dos dados
Para atualizar os dados completos, você pode:

1. Substituir o conteúdo do arquivo `load-data.js` com os dados mais recentes do seu JSON
2. Ou, se estiver usando um servidor local, modificar o código para usar fetch novamente:

```javascript
// Substitua a função loadData() no index.html
async function loadData() {
    try {
        const response = await fetch('sofascore_analysis_20251013_175158.json');
        jsonData = await response.json();
        
        // Carregar as ligas no select
        const leagueSelect = document.getElementById('league');
        leagueSelect.innerHTML = '<option value="">Selecione uma liga</option>';
        
        for (const league in jsonData) {
            const option = document.createElement('option');
            option.value = league;
            option.textContent = league;
            leagueSelect.appendChild(option);
        }
    } catch (error) {
        console.error('Erro ao carregar os dados:', error);
        alert('Erro ao carregar os dados. Verifique o console para mais detalhes.');
    }
}
```