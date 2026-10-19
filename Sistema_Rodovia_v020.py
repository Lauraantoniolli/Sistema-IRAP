import sys
import os
import math
import json

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QFileDialog,
    QVBoxLayout, QHBoxLayout, QWidget, QLabel, QLineEdit, QComboBox, QMessageBox
)
from PySide6.QtGui import QPixmap, QPainter, QPen, QBrush
from PySide6.QtCore import Qt, QPointF


class AreaImagem(QWidget):
    """Área da foto + grade métrica com perspectiva.

    Regras desta versão:
    - Os 6 pontos continuam independentes para o encaixe manual da faixa.
    - Não existe equalização automática entre os 3 níveis.
    - A referência de calibração é 191 px = 1 metro.
    - As linhas transversais são posicionadas em intervalos de 1 m no
      sentido longitudinal, usando a perspectiva estimada pelos limites.
    - A largura da grade também é convertida para metros usando a mesma
      referência na região central. A última célula pode ser parcial caso
      a largura total não seja múltipla de 1 m.
    - O cálculo fica em cache para não quebrar Mostrar/Ocultar grade.
    """

    def __init__(self):
        super().__init__()

        self.imagem = None
        self.mostrar_grade = False
        self.mostrar_offset_lateral = False
        # None = sem duplicação; "esquerda"/"direita" = segunda grade
        self.direcao_grade_duplicada = None
        # A segunda grade tem seus próprios seis pontos, independentes da principal.
        self.pontos_grade_duplicada = None
        self._direcao_pontos_grade_duplicada = None
        self._grade_mouse_duplicada = False

        # Calibração fornecida pelo usuário:
        # 191 pixels da foto = 1 metro.
        self.pixels_por_metro = 191.0

        # 0 superior esquerdo
        # 1 superior direito
        # 2 intermediário esquerdo
        # 3 intermediário direito
        # 4 inferior esquerdo
        # 5 inferior direito
        self.pontos = []

        self.cantos = []
        self.meios_laterais = []

        self.alca_selecionada = -1
        self.movendo_grade = False
        self.ultima_posicao_mouse = QPointF()

        self.raio_alca = 9
        self._ultimo_tamanho = None
        self._redimensionando_pontos = False

        # Cache: só recalcula a geometria métrica quando os pontos mudam.
        self._cache_assinatura = None
        self._cache_linhas_v = None
        self._cache_colunas_u = None
        # Cache por geometria: principal e duplicada alternam durante o paint.
        self._cache_geometrias = {}
        self._cache_ponto_fuga = {}

        # Modo de medição: seleciona dois dos seis pontos de controle.
        self.modo_medicao = False
        self.pontos_medicao = []
        self.medida_metros = None
        # Identifica em qual grade os pontos da medição foram selecionados.
        self.grade_medicao_selecionada = "principal"

        # Linha de bordo: selecionar um bordo e depois um ponto no asset
        # lateral, ou selecionar os dois bordos para obter a largura.
        self.modo_linha_bordo = False
        self.lado_bordo_selecionado = None  # 0 = esquerdo; 1 = direito
        self.grade_bordo_selecionada = None  # "principal" ou "duplicada"
        self.ponto_externo_bordo = None
        self.ponto_inicio_bordo = None
        self.medida_bordo_metros = None
        self.bordos_selecionados = set()

        # Modo Desenho: etapas guiadas e desenhos associados a cada fotografia.
        self.modo_desenho = False
        self.etapa_desenho = 0
        self.caminho_foto_atual = None
        self.desenhos_por_foto = {}
        self.tabela_desenho = {}
        self._grade_desenho_atual = "principal"
        self._lado_desenho_temporario = None
        self._ponto_desenho_temporario = None
        self._bordos_desenho_por_faixa = {}
        self.etapas_desenho_por_foto = {}
        self.selecoes_temporarias_desenho = {}
        self._rect_botoes_acostamento = {}
        self.prompt_desenho = ""

        # Persistência dos desenhos por fotografia. O conteúdo só é removido
        # pelo comando "Excluir desenho" após confirmação do usuário.
        pasta_config = os.environ.get("APPDATA")
        if pasta_config:
            pasta_config = os.path.join(pasta_config, "SistemaRodovia")
        else:
            pasta_config = os.path.join(os.path.expanduser("~"), ".sistema_rodovia")
        self.arquivo_estado_desenho = os.path.join(
            pasta_config, "desenhos_por_foto.json"
        )
        self._carregar_estado_desenho()

        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)

        self.setStyleSheet("""
            QWidget {
                background-color: #eeeeee;
                border: none;
            }
        """)

    # ------------------------------------------------------------------
    # PONTOS / GRADE BASE
    # ------------------------------------------------------------------
    def atualizar_referencias_pontos(self):
        if len(self.pontos) != 6:
            self.cantos = []
            self.meios_laterais = []
            return

        self.cantos = [
            self.pontos[0], self.pontos[1],
            self.pontos[5], self.pontos[4]
        ]
        self.meios_laterais = [
            self.pontos[2], self.pontos[3]
        ]

    def invalidar_cache_grade(self):
        self._cache_assinatura = None
        self._cache_linhas_v = None
        self._cache_colunas_u = None
        self._cache_geometrias.clear()
        self._cache_ponto_fuga.clear()

    def assinatura_grade(self):
        if len(self.pontos) != 6:
            return None
        return tuple(
            (round(p.x(), 2), round(p.y(), 2))
            for p in self.pontos
        )

    def carregar_imagem(self, caminho, criar_grade=True):
        imagem = QPixmap(caminho)
        if imagem.isNull():
            return

        self.imagem = imagem
        caminho_anterior = self.caminho_foto_atual
        if caminho_anterior:
            self.selecoes_temporarias_desenho[caminho_anterior] = self._lado_desenho_temporario
        self.caminho_foto_atual = os.path.abspath(caminho)
        self._lado_desenho_temporario = self.selecoes_temporarias_desenho.get(self.caminho_foto_atual)
        # Desenhos são mantidos somente na memória desta execução. Ao navegar,
        # os dicionários preservam cada foto; ao fechar o programa, tudo reseta.
        self.limpar_medicao()
        # Ao trocar de foto, limpa apenas a seleção/resultado anterior.
        # O estado do botão Linha de bordo continua sob controle da janela.
        self.lado_bordo_selecionado = None
        self.grade_bordo_selecionada = None
        self.ponto_externo_bordo = None
        self.ponto_inicio_bordo = None
        self.medida_bordo_metros = None
        self.bordos_selecionados = set()
        self.invalidar_cache_grade()

        if criar_grade:
            self.criar_grade_inicial()

        if self.modo_desenho:
            etapas = self._etapas_desenho()
            etapa = self.etapas_desenho_por_foto.get(self.caminho_foto_atual, 0)
            etapa = max(0, min(etapa, max(0, len(etapas) - 1)))
            self.etapa_desenho = etapa
            if etapas:
                self.prompt_desenho = (
                    "Desenho já salvo nesta foto. Use Excluir desenho para refazer."
                    if etapas[etapa].get("acao") == "fim"
                    else etapas[etapa]["prompt"]
                )

        self.update()

    def criar_grade_inicial(self):
        if self.imagem is None:
            return

        largura = self.width()
        altura = self.height()

        foto = self.imagem.scaled(
            largura,
            altura,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        x = (largura - foto.width()) / 2
        y = (altura - foto.height()) / 2

        margem_x = foto.width() * 0.20
        margem_y = foto.height() * 0.20

        esquerda = x + margem_x
        direita = x + foto.width() - margem_x
        topo = y + margem_y
        baixo = y + foto.height() - margem_y
        meio_y = (topo + baixo) / 2

        self.pontos = [
            QPointF(esquerda, topo),
            QPointF(direita, topo),
            QPointF(esquerda, meio_y),
            QPointF(direita, meio_y),
            QPointF(esquerda, baixo),
            QPointF(direita, baixo)
        ]

        self.atualizar_referencias_pontos()
        self.limpar_medicao()
        self.invalidar_cache_grade()
        if self.direcao_grade_duplicada in ("esquerda", "direita"):
            self.pontos_grade_duplicada = self.gerar_pontos_grade_duplicada(
                self.direcao_grade_duplicada
            )
            self._direcao_pontos_grade_duplicada = self.direcao_grade_duplicada

    def alternar_medicao(self, ativada):
        self.botao_medicao.setText("✏️ Parar medição" if ativada else "✏️ Medir")
        self.area_imagem.alternar_medicao(ativada)

    def alternar_grade_duplicada(self, indice):
        """Liga/desliga a segunda grade e cria seus seis pontos independentes."""
        area = self.area_imagem
        nova_direcao = self.seletor_grade_duplicada.currentData()
        direcao_anterior = area.direcao_grade_duplicada
        area.direcao_grade_duplicada = nova_direcao

        if nova_direcao is None:
            area.pontos_grade_duplicada = None
            area._direcao_pontos_grade_duplicada = None
            # Desativar a segunda grade também remove Faixa 2 da tabela da foto atual.
            if area.caminho_foto_atual:
                area.tabela_desenho.setdefault(area.caminho_foto_atual, {}).pop("Faixa 2", None)
            if area.modo_desenho:
                area.etapa_desenho = 0
                area.etapas_desenho_por_foto[area.caminho_foto_atual] = 0
                area._lado_desenho_temporario = None
                area.selecoes_temporarias_desenho[area.caminho_foto_atual] = None
                etapas = area._etapas_desenho()
                area.prompt_desenho = etapas[0]["prompt"] if etapas else ""
        elif (nova_direcao != direcao_anterior
              or len(area.pontos_grade_duplicada or []) != 6):
            area.pontos_grade_duplicada = area.gerar_pontos_grade_duplicada(nova_direcao)
            area._direcao_pontos_grade_duplicada = nova_direcao
            if area.modo_desenho:
                area.etapa_desenho = 0
                if area.caminho_foto_atual:
                    area.etapas_desenho_por_foto[area.caminho_foto_atual] = 0
                    area.selecoes_temporarias_desenho[area.caminho_foto_atual] = None
                    area.tabela_desenho.setdefault(area.caminho_foto_atual, {}).setdefault("Faixa 2", {"largura": None})
                area._lado_desenho_temporario = None
                etapas = area._etapas_desenho()
                area.prompt_desenho = etapas[0]["prompt"] if etapas else ""

        area.invalidar_cache_grade()
        area._salvar_estado_desenho()
        area.update()

    def alternar_offset(self, ativada):
        self.botao_offset.setText(
            "☑ Offset lateral" if ativada else "↔ Offset lateral"
        )
        self.area_imagem.alternar_offset_lateral(ativada)

    def alternar_grade(self, ativada):
        self.mostrar_grade = ativada
        self.update()

    def alternar_desenho(self, ativado):
        """Mostra/oculta os desenhos e retoma o passo guardado para a foto."""
        self.modo_desenho = bool(ativado)
        if not self.modo_desenho:
            self._salvar_estado_desenho()
            self.update()
            return

        self.modo_medicao = False
        self.modo_linha_bordo = False
        duas = self._duas_faixas_ativas()
        if self.caminho_foto_atual:
            tabela = self.tabela_desenho.setdefault(self.caminho_foto_atual, {})
            tabela.setdefault("Faixa 1", {}).setdefault("largura", None)
            if duas:
                tabela.setdefault("Faixa 2", {}).setdefault("largura", None)
            else:
                # Mantém os dados salvos da Faixa 2; apenas a exibição da linha
                # da tabela fica condicionada à duplicação ativa.
                pass
            acostamentos = tabela.setdefault("_acostamentos", {})
            acostamentos.setdefault("direita", None)
            acostamentos.setdefault("esquerda", None)

            etapas = self._etapas_desenho()
            etapa = self.etapas_desenho_por_foto.get(self.caminho_foto_atual, 0)
            self.etapa_desenho = max(0, min(etapa, max(0, len(etapas) - 1)))
            if etapas:
                self.prompt_desenho = (
                    "Desenho concluído. Use Excluir desenho para refazer esta foto."
                    if etapas[self.etapa_desenho].get("acao") == "fim"
                    else etapas[self.etapa_desenho]["prompt"]
                )
            self._salvar_estado_desenho()
        self.alca_selecionada = -1
        self.movendo_grade = False
        self.update()

    def _duas_faixas_ativas(self):
        return (
            self.direcao_grade_duplicada in ("esquerda", "direita")
            and len(self.pontos_grade_duplicada or []) == 6
        )

    def _etapas_desenho(self):
        """Fluxo guiado: larguras das faixas e dois acostamentos globais."""
        duas = self._duas_faixas_ativas()
        etapas = []

        if not duas:
            etapas.extend([
                {"acao": "linha", "tipo": "bordo", "cor": "verde",
                 "grade": "principal", "faixa": "Faixa 1", "lado": 1,
                 "papel": "bordo_largura", "prompt":
                 "Desenho: clique em um dos 3 pontos da linha de bordo da direita."},
                {"acao": "linha", "tipo": "bordo", "cor": "verde",
                 "grade": "principal", "faixa": "Faixa 1", "lado": 0,
                 "papel": "bordo_largura", "finaliza_largura": "bordos",
                 "prompt":
                 "Desenho: clique em um dos 3 pontos da linha de bordo da esquerda."},
            ])
        else:
            # A grade principal é a Faixa 1. A faixa interna/centro é a borda
            # compartilhada com a grade secundária, e depende da duplicação.
            lado_externo_faixa1 = 1 if self.direcao_grade_duplicada == "esquerda" else 0
            lado_centro_faixa1 = 1 - lado_externo_faixa1
            lado_externo_faixa2 = 0 if self.direcao_grade_duplicada == "esquerda" else 1
            lado_centro_faixa2 = 1 - lado_externo_faixa2
            etapas.extend([
                {"acao": "linha", "tipo": "bordo", "cor": "verde",
                 "grade": "principal", "faixa": "Faixa 1", "lado": lado_externo_faixa1,
                 "papel": "bordo_largura", "prompt":
                 "Desenho: clique em um dos 3 pontos da linha de bordo da Faixa 1."},
                {"acao": "linha", "tipo": "centro", "cor": "amarelo",
                 "grade": "principal", "faixa": "Faixa 1", "lado": lado_centro_faixa1,
                 "papel": "centro_largura", "finaliza_largura": "bordo_centro",
                 "prompt":
                 "Desenho: clique em um dos 3 pontos da linha central da Faixa 1."},
                {"acao": "linha", "tipo": "bordo", "cor": "verde",
                 "grade": "duplicada", "faixa": "Faixa 2", "lado": lado_externo_faixa2,
                 "papel": "bordo_largura", "prompt":
                 "Desenho: clique em um dos 3 pontos da linha de bordo da Faixa 2."},
                {"acao": "linha", "tipo": "centro", "cor": "amarelo",
                 "grade": "duplicada", "faixa": "Faixa 2", "lado": lado_centro_faixa2,
                 "papel": "centro_largura", "finaliza_largura": "bordo_centro",
                 "prompt":
                 "Desenho: clique em um dos 3 pontos da linha central da Faixa 2."},
            ])

        # Os acostamentos são globais: sempre um direito e um esquerdo.
        if not duas:
            bordo_dir = ("principal", 1)
            bordo_esq = ("principal", 0)
        elif self.direcao_grade_duplicada == "esquerda":
            bordo_dir = ("principal", 1)
            bordo_esq = ("duplicada", 0)
        else:
            bordo_dir = ("duplicada", 1)
            bordo_esq = ("principal", 0)

        for nome_lado, exibicao, alvo in (
            ("direita", "direito", bordo_dir),
            ("esquerda", "esquerdo", bordo_esq),
        ):
            grade, lado = alvo
            etapas.append({
                "acao": "decisao_acostamento", "grade": grade, "lado": lado,
                "nome_lado": nome_lado, "papel": f"acostamento_{nome_lado}",
                "prompt": f"Desenho: a via possui acostamento {exibicao}?"
            })
            etapas.append({
                "acao": "selecionar_bordo_acostamento", "tipo": "bordo",
                "cor": "verde", "grade": grade, "faixa": "Acostamentos",
                "lado": lado, "papel": f"selecao_bordo_acostamento_{nome_lado}",
                "nome_lado": nome_lado,
                "prompt": f"Desenho: clique em um dos 3 pontos da linha de bordo do acostamento {exibicao}."
            })
            etapas.append({
                "acao": "medir_acostamento", "tipo": "offset",
                "cor": "vermelho", "grade": grade, "faixa": "Acostamentos",
                "lado": lado, "papel": f"acostamento_{nome_lado}",
                "nome_lado": nome_lado,
                "prompt": f"Desenho: clique em qualquer ponto dentro do offset lateral do acostamento {exibicao}. Será usada a transversal tracejada mais próxima."
            })

        etapas.append({
            "acao": "fim",
            "prompt": "Desenho concluído. As linhas e as medidas desta foto foram salvas."
        })
        return etapas

    def _grade_e_pontos_desenho(self, nome):
        if nome == "duplicada" and len(self.pontos_grade_duplicada or []) == 6:
            return self.pontos_grade_duplicada
        return self.pontos

    def _achar_bordo_desenho(self, posicao, nome_grade, tipo="bordo", lado_forcado=None):
        """Identifica uma curva lateral existente a partir de qualquer ponto de controle."""
        pontos_principais = self.pontos
        pontos_alvo = self._grade_e_pontos_desenho(nome_grade)
        if len(pontos_alvo or []) != 6:
            return (float("inf"), 0, 0.0, QPointF())
        try:
            self.pontos = pontos_alvo
            self.atualizar_referencias_pontos()
            lados = (lado_forcado,) if lado_forcado in (0, 1) else (0, 1)
            melhor = (float("inf"), 0, 0.0, QPointF())
            for lado in lados:
                indices = (0, 2, 4) if lado == 0 else (1, 3, 5)
                for indice, v_controle in zip(indices, (0.0, 0.5, 1.0)):
                    controle = pontos_alvo[indice]
                    d = self.distancia(posicao, controle)
                    if d < melhor[0]:
                        curva = self.curva_lateral(lado, v_controle)
                        melhor = (d, lado, v_controle, QPointF(curva.x(), curva.y()))
                for i in range(121):
                    v = i / 120
                    ponto = self.curva_lateral(lado, v)
                    d = self.distancia(posicao, ponto)
                    if d < melhor[0]:
                        melhor = (d, lado, v, QPointF(ponto.x(), ponto.y()))
            return melhor
        finally:
            self.pontos = pontos_principais
            self.atualizar_referencias_pontos()

    def _medir_largura_desenho(self, selecao_anterior, selecao_atual, modo):
        """Mede a distância entre as duas curvas selecionadas no mesmo nível."""
        if not selecao_anterior or not selecao_atual:
            return None
        if selecao_anterior.get("grade") != selecao_atual.get("grade"):
            return None
        nome_grade = selecao_atual["grade"]
        pontos_principais = self.pontos
        pontos_alvo = self._grade_e_pontos_desenho(nome_grade)
        if len(pontos_alvo or []) != 6:
            return None
        v = max(0.0, min(1.0, (selecao_anterior["v"] + selecao_atual["v"]) / 2.0))
        lado_a, lado_b = selecao_anterior.get("lado"), selecao_atual.get("lado")
        if lado_a not in (0, 1) or lado_b not in (0, 1) or lado_a == lado_b:
            return None
        try:
            self.pontos = pontos_alvo
            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()
            p1 = self.curva_lateral(lado_a, v)
            p2 = self.curva_lateral(lado_b, v)
            return self._medir_pontos_metros(p1, p2)
        finally:
            self.pontos = pontos_principais
            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()

    def _medir_offset_grade_desenho(self, nome_grade, lado, posicao):
        """Mede o afastamento no offset da grade correta, sem depender da grade clicada."""
        if not self.mostrar_offset_lateral:
            return None, None, None
        pontos_principais = self.pontos
        pontos_alvo = self._grade_e_pontos_desenho(nome_grade)
        if len(pontos_alvo or []) != 6:
            return None, None, None
        try:
            self.pontos = pontos_alvo
            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()
            return self.medir_bordo_ate_ponto(lado, posicao, modo_desenho=True)
        finally:
            self.pontos = pontos_principais
            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()

    def _medir_pontos_metros(self, p1, p2):
        calibracao = self.calibracao_perspectiva_medicao()
        if calibracao is None:
            return self.distancia(p1, p2) / max(1.0, self.pixels_por_metro_na_tela())
        h, sx, sy, _ = calibracao
        a = self.aplicar_homografia(p1, h)
        b = self.aplicar_homografia(p2, h)
        if a is None or b is None:
            return self.distancia(p1, p2) / max(1.0, self.pixels_por_metro_na_tela())
        return math.hypot((b.x()-a.x())*sx, (b.y()-a.y())*sy)

    def _salvar_estado_desenho(self):
        """Persiste os desenhos, tabela e etapa de cada foto em arquivo JSON."""
        caminho_tmp = self.arquivo_estado_desenho + ".tmp"
        selecoes_salvas = {}
        for caminho, selecao in self.selecoes_temporarias_desenho.items():
            if isinstance(selecao, dict):
                # O QPointF do clique não é serializado; a medição usa grade,
                # lado e parâmetro v, que são suficientes para retomar a etapa.
                selecoes_salvas[caminho] = {
                    chave: valor for chave, valor in selecao.items()
                    if chave != "ponto"
                }
            else:
                selecoes_salvas[caminho] = None

        dados = {
            "versao": 1,
            "desenhos_por_foto": self.desenhos_por_foto,
            "tabela_desenho": self.tabela_desenho,
            "etapas_desenho_por_foto": self.etapas_desenho_por_foto,
            "selecoes_temporarias_desenho": selecoes_salvas,
        }
        try:
            os.makedirs(os.path.dirname(self.arquivo_estado_desenho), exist_ok=True)
            with open(caminho_tmp, "w", encoding="utf-8") as arquivo:
                json.dump(dados, arquivo, ensure_ascii=False, separators=(",", ":"))
            os.replace(caminho_tmp, self.arquivo_estado_desenho)
        except (OSError, TypeError, ValueError) as erro:
            # O programa continua operando mesmo se o local de configuração
            # estiver indisponível; registra o motivo no terminal para diagnóstico.
            print(f"Não foi possível salvar os desenhos: {erro}")
            try:
                if os.path.exists(caminho_tmp):
                    os.remove(caminho_tmp)
            except OSError:
                pass

    def _carregar_estado_desenho(self):
        """Restaura os desenhos persistentes sem ativar o modo Desenho."""
        try:
            with open(self.arquivo_estado_desenho, "r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as erro:
            print(f"Não foi possível carregar os desenhos salvos: {erro}")
            return

        if not isinstance(dados, dict):
            return

        def normalizar_mapa(mapa, tipo_esperado):
            resultado = {}
            if not isinstance(mapa, dict):
                return resultado
            for caminho, valor in mapa.items():
                if not isinstance(caminho, str) or not isinstance(valor, tipo_esperado):
                    continue
                resultado[os.path.abspath(caminho)] = valor
            return resultado

        self.desenhos_por_foto = normalizar_mapa(
            dados.get("desenhos_por_foto", {}), list
        )
        self.tabela_desenho = normalizar_mapa(
            dados.get("tabela_desenho", {}), dict
        )
        self.etapas_desenho_por_foto = normalizar_mapa(
            dados.get("etapas_desenho_por_foto", {}), int
        )
        selecoes = normalizar_mapa(
            dados.get("selecoes_temporarias_desenho", {}), (dict, type(None))
        )
        # Garante que seleção restaurada contém apenas dados simples.
        self.selecoes_temporarias_desenho = {}
        for caminho, selecao in selecoes.items():
            if isinstance(selecao, dict):
                self.selecoes_temporarias_desenho[caminho] = {
                    chave: valor for chave, valor in selecao.items()
                    if chave != "ponto"
                }
            else:
                self.selecoes_temporarias_desenho[caminho] = None

    def excluir_desenho_atual(self):
        """Limpa somente a foto atual; a confirmação é feita pela janela."""
        if not self.caminho_foto_atual:
            return
        caminho = self.caminho_foto_atual
        self.desenhos_por_foto.pop(caminho, None)
        self.tabela_desenho.pop(caminho, None)
        self.etapas_desenho_por_foto[caminho] = 0
        self.selecoes_temporarias_desenho[caminho] = None
        self._lado_desenho_temporario = None
        self._ponto_desenho_temporario = None
        self._bordos_desenho_por_faixa = {}
        self.etapa_desenho = 0
        self._salvar_estado_desenho()
        self.update()

    def _pontos_segmento_largura_desenho(self, selecao_a, selecao_b):
        """Obtém os extremos da cota de largura no mesmo nível longitudinal."""
        if not selecao_a or not selecao_b:
            return None, None
        if selecao_a.get("grade") != selecao_b.get("grade"):
            return None, None
        grade_nome = selecao_b.get("grade", "principal")
        pontos_grade = self._grade_e_pontos_desenho(grade_nome)
        lado_a, lado_b = selecao_a.get("lado"), selecao_b.get("lado")
        if len(pontos_grade or []) != 6 or lado_a not in (0, 1) or lado_b not in (0, 1) or lado_a == lado_b:
            return None, None
        v = max(0.0, min(1.0, (float(selecao_a.get("v", 0.5)) + float(selecao_b.get("v", 0.5))) / 2.0))
        pontos_principais = self.pontos
        try:
            self.pontos = pontos_grade
            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()
            return QPointF(self.curva_lateral(lado_a, v)), QPointF(self.curva_lateral(lado_b, v))
        finally:
            self.pontos = pontos_principais
            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()

    def _registrar_segmento_medicao(self, p1, p2, metros, tipo, faixa, papel, cor="magenta"):
        """Guarda a cota dibujada y su etiqueta en la foto actual."""
        if not self.caminho_foto_atual or p1 is None or p2 is None or metros is None:
            return None
        w, h = max(1, self.width()), max(1, self.height())
        linha = {
            "tipo": tipo, "cor": cor,
            "pontos": [[p1.x() / w, p1.y() / h], [p2.x() / w, p2.y() / h]],
            "grade": faixa, "faixa": faixa, "papel": papel,
            "metros": float(metros), "rotulo": f"{float(metros):.2f} m"
        }
        self.desenhos_por_foto.setdefault(self.caminho_foto_atual, []).append(linha)
        return linha

    def _registrar_segmento_acostamento(self, ponto_bordo, ponto_offset, nome_grade,
                                         lado, metros, faixa, papel):
        """Salva somente o segmento transversal selecionado no offset tracejado.

        O acostamento deve ser representado sobre a linha tracejada transversal
        existente, do bordo até o ponto projetado pelo método de medição. Não
        deve desenhar a curva longitudinal inteira do offset lateral.
        """
        if not self.caminho_foto_atual or ponto_bordo is None or ponto_offset is None:
            return None
        w, h = max(1, self.width()), max(1, self.height())
        pts = [ponto_bordo, ponto_offset]
        linha = {
            "tipo": "segmento_acostamento", "cor": "vermelho",
            "pontos": [[pt.x() / w, pt.y() / h] for pt in pts],
            "grade": nome_grade, "faixa": faixa,
            "papel": papel, "lado": lado, "metros": metros,
            "rotulo": f"{float(metros):.2f} m" if isinstance(metros, (int, float)) else ""
        }
        self.desenhos_por_foto.setdefault(self.caminho_foto_atual, []).append(linha)
        return pts

    def _registrar_linha_desenho(self, tipo, nome_grade, lado, v, cor, metros=None,
                                 faixa=None, papel=None):
        pontos_grade = self._grade_e_pontos_desenho(nome_grade)
        if len(pontos_grade or []) != 6 or not self.caminho_foto_atual:
            return None
        principais = self.pontos
        try:
            self.pontos = pontos_grade
            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()
            if tipo in ("bordo", "centro"):
                # A linha amarela é a borda interna/compartilhada que o usuário
                # escolheu, não a média geométrica entre as duas bordas.
                pts = [self.curva_lateral(lado, i / 120) for i in range(121)]
            elif tipo == "offset":
                pts = [self.ponto_offset_lateral(lado, max(0.05, float(metros or 1.0)), i / 160) for i in range(161)]
            else:
                return None
        finally:
            self.pontos = principais
            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()
        w, h = max(1, self.width()), max(1, self.height())
        linha = {
            "tipo": tipo, "cor": cor,
            "pontos": [[pt.x()/w, pt.y()/h] for pt in pts],
            "grade": nome_grade, "faixa": faixa, "papel": papel,
            "lado": lado, "metros": metros
        }
        self.desenhos_por_foto.setdefault(self.caminho_foto_atual, []).append(linha)
        return pts

    def distancia(self, p1, p2):
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        return math.hypot(dx, dy)

    def escala_tela(self):
        """Pixels exibidos por pixel da imagem original.

        A janela é dimensionada para manter a proporção da foto, portanto
        usamos a escala horizontal como referência.
        """
        if self.imagem is None or self.imagem.width() <= 0:
            return 1.0
        return self.width() / self.imagem.width()

    def pixels_por_metro_na_tela(self):
        return max(1.0, self.pixels_por_metro * self.escala_tela())

    def curva_lateral(self, lado, t):
        """Curva Bézier quadrática de cada borda da faixa."""
        if len(self.pontos) != 6:
            return QPointF()

        if lado == 0:
            p0 = self.pontos[0]
            pm = self.pontos[2]
            p1 = self.pontos[4]
        else:
            p0 = self.pontos[1]
            pm = self.pontos[3]
            p1 = self.pontos[5]

        controle = QPointF(
            2 * pm.x() - (p0.x() + p1.x()) / 2,
            2 * pm.y() - (p0.y() + p1.y()) / 2
        )

        a = (1 - t) ** 2
        b = 2 * (1 - t) * t
        c = t ** 2

        return QPointF(
            a * p0.x() + b * controle.x() + c * p1.x(),
            a * p0.y() + b * controle.y() + c * p1.y()
        )

    def ponto_grade(self, u, v):
        esquerda = self.curva_lateral(0, v)
        direita = self.curva_lateral(1, v)

        return QPointF(
            (1 - u) * esquerda.x() + u * direita.x(),
            (1 - u) * esquerda.y() + u * direita.y()
        )

    def centro_linha(self, v):
        esquerda = self.curva_lateral(0, v)
        direita = self.curva_lateral(1, v)
        return QPointF(
            (esquerda.x() + direita.x()) / 2,
            (esquerda.y() + direita.y()) / 2
        )

    def largura_nivel(self, v):
        return self.distancia(
            self.curva_lateral(0, v),
            self.curva_lateral(1, v)
        )

    def comprimento_curva_centro(self, v0, v1, amostras=80):
        """Comprimento em pixels do eixo central entre dois valores de v."""
        if v1 <= v0:
            return 0.0

        total = 0.0
        anterior = self.centro_linha(v0)

        for i in range(1, amostras + 1):
            v = v0 + (v1 - v0) * i / amostras
            atual = self.centro_linha(v)
            total += self.distancia(anterior, atual)
            anterior = atual

        return total

    # ------------------------------------------------------------------
    # MEDIÇÃO
    # ------------------------------------------------------------------
    def alternar_offset_lateral(self, ativado):
        self.mostrar_offset_lateral = ativado
        self.update()

    def limpar_medicao(self):
        self.pontos_medicao = []
        self.medida_metros = None
        self.grade_medicao_selecionada = "principal"
        self.update()

    def alternar_medicao(self, ativada):
        self.modo_medicao = ativada
        self.limpar_medicao()
        self.update()

    def escala_local_pixels_por_metro(self, v):
        """Estima a escala local em pixels/metro pela perspectiva.

        A referência continua sendo 191 px = 1 m em v=0.5. A escala
        diminui no fundo e aumenta na parte próxima, sem alterar os seis
        pontos da grade.
        """
        ppm_ref = self.pixels_por_metro_na_tela()
        vf = self.estimar_ponto_de_fuga_v()
        if vf is None or vf <= 1.01:
            return ppm_ref

        # z(v)=v/(vf-v). A derivada representa a variação de profundidade
        # projetada. Usamos a razão da derivada em relação a v=0.5.
        def derivada(v):
            den = max(vf - v, 1e-6)
            return vf / (den * den)

        d_ref = derivada(0.5)
        d_local = derivada(max(0.0, min(1.0, v)))
        if d_ref <= 0:
            return ppm_ref

        escala = ppm_ref * (d_local / d_ref)
        return max(1.0, escala)

    def v_do_ponto_medicao(self, indice):
        if indice in (0, 1):
            return 0.0
        if indice in (2, 3):
            return 0.5
        return 1.0

    def alternar_linha_bordo(self, ativada):
        self.modo_linha_bordo = ativada
        self.lado_bordo_selecionado = None
        self.grade_bordo_selecionada = None
        self.ponto_externo_bordo = None
        self.ponto_inicio_bordo = None
        self.medida_bordo_metros = None
        self.bordos_selecionados = set()
        self.update()

    def ponto_mais_proximo_bordo(self, posicao, lado, amostras=500):
        """Encontra a posição da curva lateral mais próxima do clique."""
        melhor_v = 0.0
        melhor_ponto = self.curva_lateral(lado, 0.0)
        melhor_distancia = float("inf")
        for i in range(amostras + 1):
            v = i / amostras
            ponto = self.curva_lateral(lado, v)
            d = self.distancia(posicao, ponto)
            if d < melhor_distancia:
                melhor_v = v
                melhor_ponto = ponto
                melhor_distancia = d
        return melhor_v, melhor_ponto, melhor_distancia

    def lados_bordo_externos_disponiveis(self):
        """Lista apenas os bordos que representam os offsets externos da pista."""
        if self.direcao_grade_duplicada == "esquerda":
            return [("principal", 1), ("duplicada", 0)]
        if self.direcao_grade_duplicada == "direita":
            return [("principal", 0), ("duplicada", 1)]
        return [("principal", 0), ("principal", 1)]

    def detectar_linha_bordo(self, posicao, tolerancia=14.0):
        """Retorna (grade, lado) para um bordo externo válido."""
        resultados = []
        principais = self.pontos
        duplicados = self.pontos_grade_duplicada
        try:
            for nome_grade, lado in self.lados_bordo_externos_disponiveis():
                if nome_grade == "duplicada":
                    if len(duplicados or []) != 6:
                        continue
                    self.pontos = duplicados
                else:
                    self.pontos = principais
                _, _, distancia = self.ponto_mais_proximo_bordo(posicao, lado)
                resultados.append((distancia, nome_grade, lado))
        finally:
            self.pontos = principais
            self.atualizar_referencias_pontos()

        if not resultados:
            return None
        distancia_minima, nome_grade, lado = min(resultados)
        return (nome_grade, lado) if distancia_minima <= tolerancia else None

    def projetar_ponto_em_segmento(self, ponto, a, b):
        """Retorna (distância, ponto projetado, t) para um segmento AB."""
        abx = b.x() - a.x()
        aby = b.y() - a.y()
        comprimento2 = abx * abx + aby * aby
        if comprimento2 <= 1e-12:
            return self.distancia(ponto, a), QPointF(a.x(), a.y()), 0.0

        t = ((ponto.x() - a.x()) * abx + (ponto.y() - a.y()) * aby) / comprimento2
        t = max(0.0, min(1.0, t))
        projetado = QPointF(a.x() + t * abx, a.y() + t * aby)
        return self.distancia(ponto, projetado), projetado, t

    def _ponto_em_poligono(self, ponto, poligono):
        """Teste ponto-polígono simples, usado para aceitar cliques em toda a área do offset."""
        if len(poligono) < 3:
            return False
        dentro = False
        j = len(poligono) - 1
        for i, pi in enumerate(poligono):
            pj = poligono[j]
            yi, yj = pi.y(), pj.y()
            if (yi > ponto.y()) != (yj > ponto.y()):
                x_intersecao = (pj.x() - pi.x()) * (ponto.y() - yi) / (yj - yi) + pi.x()
                if ponto.x() < x_intersecao:
                    dentro = not dentro
            j = i
        return dentro

    def medir_bordo_ate_ponto(self, lado, posicao, modo_desenho=False):
        """Projeta o clique na transversal mais próxima do offset lateral.

        O modo Desenho aceita qualquer clique dentro da área lateral e procura
        somente as três transversais de referência (topo, meio e fundo). A
        função antiga Offset lateral mantém seu comportamento/tolerância.
        """
        if len(self.pontos or []) != 6:
            return None, None, None
        quantidade = self.quantidade_offsets_laterais()

        if modo_desenho:
            # Delimita a área entre a borda selecionada e a última linha do offset.
            amostras = 60
            borda = [self.curva_lateral(lado, i / amostras) for i in range(amostras + 1)]
            externa = [self.ponto_offset_lateral(lado, quantidade, i / amostras)
                       for i in range(amostras + 1)]
            area_offset = externa + list(reversed(borda))
            if not self._ponto_em_poligono(posicao, area_offset):
                return None, None, None
            referencias_v = (0.0, 0.5, 1.0)
        else:
            try:
                _, linhas_v = self.geometria_grade_metrica()
            except Exception:
                linhas_v = [0.0, 0.5, 1.0]
            referencias_v = sorted(set([0.0, 0.5, 1.0] + [float(v) for v in linhas_v]))

        melhor = None
        for v_ref in referencias_v:
            bordo_ponto = self.curva_lateral(lado, v_ref)
            externo = self.ponto_offset_lateral(lado, quantidade, v_ref)
            distancia, projetado, t = self.projetar_ponto_em_segmento(posicao, bordo_ponto, externo)
            if melhor is None or distancia < melhor[0]:
                melhor = (distancia, v_ref, bordo_ponto, projetado, t)
        if melhor is None:
            return None, None, None

        distancia, _, bordo_ponto, projetado, t = melhor
        if not modo_desenho and distancia > max(48.0, self.raio_alca * 5):
            return None, None, None

        # A referência lateral existente usa a distância fracionária ao longo
        # da transversal, escalada pelo número de intervalos de 1 metro.
        metros = max(0.0, min(float(quantidade), float(t) * quantidade))
        if not math.isfinite(metros):
            return None, None, None
        return metros, QPointF(bordo_ponto.x(), bordo_ponto.y()), QPointF(projetado.x(), projetado.y())

    def _resolver_sistema_8x8(self, matriz, termos):
        """Resolve um sistema linear 8x8 sem depender de numpy/OpenCV."""
        n = 8
        a = [list(matriz[i]) + [float(termos[i])] for i in range(n)]

        for col in range(n):
            pivo = max(range(col, n), key=lambda r: abs(a[r][col]))
            if abs(a[pivo][col]) < 1e-10:
                return None

            if pivo != col:
                a[col], a[pivo] = a[pivo], a[col]

            valor = a[col][col]
            for j in range(col, n + 1):
                a[col][j] /= valor

            for r in range(n):
                if r == col:
                    continue
                fator = a[r][col]
                if abs(fator) < 1e-12:
                    continue
                for j in range(col, n + 1):
                    a[r][j] -= fator * a[col][j]

        return [a[i][n] for i in range(n)]

    def calcular_homografia(self):
        """Mapeia a quadrilateral da estrada para um plano retangular.

        Os quatro cantos definem a perspectiva. A transformação permite
        comparar distâncias no plano da estrada, em vez de comparar
        diretamente pixels da fotografia.
        """
        if len(self.pontos) != 6:
            return None

        origem = [
            self.pontos[0],  # superior esquerdo
            self.pontos[1],  # superior direito
            self.pontos[5],  # inferior direito
            self.pontos[4],  # inferior esquerdo
        ]
        destino = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

        matriz = []
        termos = []

        for p, (u, v) in zip(origem, destino):
            x = p.x()
            y = p.y()

            matriz.append([
                x, y, 1.0, 0.0, 0.0, 0.0,
                -u * x, -u * y
            ])
            termos.append(u)

            matriz.append([
                0.0, 0.0, 0.0, x, y, 1.0,
                -v * x, -v * y
            ])
            termos.append(v)

        solucao = self._resolver_sistema_8x8(matriz, termos)
        if solucao is None:
            return None

        return (
            solucao[0], solucao[1], solucao[2],
            solucao[3], solucao[4], solucao[5],
            solucao[6], solucao[7], 1.0
        )

    def aplicar_homografia(self, ponto, h):
        if h is None:
            return None

        x = ponto.x()
        y = ponto.y()
        den = h[6] * x + h[7] * y + h[8]

        if abs(den) < 1e-10:
            return None

        u = (h[0] * x + h[1] * y + h[2]) / den
        v = (h[3] * x + h[4] * y + h[5]) / den
        return QPointF(u, v)

    def ponto_centro_para_v(self, v):
        return self.centro_linha(max(0.0, min(1.0, v)))

    def calibracao_perspectiva_medicao(self):
        """Retorna a transformação métrica da grade.

        A referência do usuário permanece exatamente 191 px = 1 m na
        linha central. A homografia corrige a perspectiva para que a
        mesma distância real não seja interpretada como vários metros
        apenas porque aparece maior ou menor na fotografia.
        """
        h = self.calcular_homografia()
        if h is None:
            return None

        ppm = self.pixels_por_metro_na_tela()
        centro_esq = self.aplicar_homografia(self.pontos[2], h)
        centro_dir = self.aplicar_homografia(self.pontos[3], h)
        centro = self.aplicar_homografia(self.centro_linha(0.5), h)

        if centro_esq is None or centro_dir is None or centro is None:
            return None

        du = abs(centro_dir.x() - centro_esq.x())
        if du < 1e-8:
            return None

        # A largura física da linha central é calibrada pela referência
        # fornecida: 191 px na tela correspondem a 1 metro.
        largura_centro_m = self.largura_nivel(0.5) / ppm
        escala_x = largura_centro_m / du

        # Para a profundidade usamos a escala local no centro.
        # Não exigimos que existam 191 px disponíveis entre o centro e a
        # borda da foto: os 191 px são uma referência de escala, não uma
        # distância mínima que precise caber inteira dentro da grade.
        #
        # Calculamos quantos pixels aparecem por unidade de v e quanto o
        # eixo perspectivo varia em v. Assim obtemos a quantidade de
        # metros correspondente a qualquer ponto da grade sem travar a
        # linha central.
        v_ref = 0.5
        delta = 0.0005
        v_a = max(0.0, v_ref - delta)
        v_b = min(1.0, v_ref + delta)

        tela_a = self.ponto_centro_para_v(v_a)
        tela_b = self.ponto_centro_para_v(v_b)
        plano_a = self.aplicar_homografia(tela_a, h)
        plano_b = self.aplicar_homografia(tela_b, h)

        escala_y = None
        dv = v_b - v_a
        if dv > 1e-10 and plano_a is not None and plano_b is not None:
            pixels_por_v = self.distancia(tela_a, tela_b) / dv
            plano_y_por_v = abs(plano_b.y() - plano_a.y()) / dv

            if pixels_por_v > 1e-8 and plano_y_por_v > 1e-10:
                # 191 px (na escala da tela) = 1 metro.
                # Portanto, a variação de plano necessária para 1 metro é
                # determinada pela escala local no centro.
                escala_y = pixels_por_v / (ppm * plano_y_por_v)

        if escala_y is None or not math.isfinite(escala_y) or escala_y <= 0:
            escala_y = escala_x

        return h, escala_x, escala_y, centro

    def distancia_medida_em_metros(self, i, j):
        """Mede pontos na mesma transformação métrica da via.

        A homografia é calculada uma única vez com a grade principal, que é
        a referência geométrica calibrada. Os pontos da grade duplicada são
        projetados nessa mesma homografia, inclusive fora do quadrilátero
        principal. Isso é intencional: recalcular uma homografia independente
        para cada grade força cada quadrilátero a ocupar um quadrado unitário
        e pode apagar a diferença de escala causada pela perspectiva.

        A calibração de 191 px/m é aplicada na linha central da grade principal.
        A homografia converte a posição dos pontos para coordenadas do plano;
        as escalas X/Y convertem essas coordenadas para metros.
        """
        if len(self.pontos or []) != 6:
            return 0.0

        pontos_principais = self.pontos
        pontos_medidos = (
            self.pontos_grade_duplicada
            if self.grade_medicao_selecionada == "duplicada"
            and len(self.pontos_grade_duplicada or []) == 6
            else pontos_principais
        )
        if len(pontos_medidos or []) != 6:
            return 0.0

        calibracao = self.calibracao_perspectiva_medicao()
        if calibracao is None:
            return self.distancia(pontos_medidos[i], pontos_medidos[j]) / max(
                1.0, self.pixels_por_metro_na_tela()
            )

        h_principal, escala_x, escala_y, _ = calibracao
        # Importante: NÃO calcular homografia independente para a duplicada.
        # A mesma transformação da via permite que pixels/metro varie com a
        # profundidade e com o tamanho/reposicionamento dos pontos da grade.
        p1 = self.aplicar_homografia(pontos_medidos[i], h_principal)
        p2 = self.aplicar_homografia(pontos_medidos[j], h_principal)
        if p1 is None or p2 is None:
            return 0.0

        dx = (p2.x() - p1.x()) * escala_x
        dy = (p2.y() - p1.y()) * escala_y
        resultado = math.hypot(dx, dy)
        return resultado if math.isfinite(resultado) else 0.0

    # ------------------------------------------------------------------
    # PERSPECTIVA MÉTRICA
    # ------------------------------------------------------------------
    def estimar_ponto_de_fuga_v(self):
        """Estima a posição do ponto de fuga usando as duas bordas.

        Para a perspectiva longitudinal, prolongamos as retas que ligam
        topo/fundo de cada lado. O resultado é convertido para o parâmetro
        v da grade.

        Se as bordas forem curvas, usamos os extremos, porque eles são a
        parte que define a direção longitudinal principal da estrada.
        """
        if len(self.pontos) != 6:
            return None

        assinatura = self.assinatura_grade()
        if assinatura in self._cache_ponto_fuga:
            return self._cache_ponto_fuga[assinatura]

        l0, l1 = self.pontos[0], self.pontos[4]
        r0, r1 = self.pontos[1], self.pontos[5]

        dx1 = l1.x() - l0.x()
        dy1 = l1.y() - l0.y()
        dx2 = r1.x() - r0.x()
        dy2 = r1.y() - r0.y()

        den = dx1 * dy2 - dy1 * dx2
        if abs(den) < 1e-8:
            self._cache_ponto_fuga[assinatura] = None
            return None

        # Interseção de l0 + t*(l1-l0) com r0 + s*(r1-r0).
        t = ((r0.x() - l0.x()) * dy2 -
             (r0.y() - l0.y()) * dx2) / den

        x = l0.x() + t * dx1
        y = l0.y() + t * dy1

        # Converter o ponto de fuga para o parâmetro v usando a projeção
        # sobre o eixo central. Fazemos uma busca simples e robusta.
        vf = self.v_mais_proximo_do_ponto(QPointF(x, y))
        self._cache_ponto_fuga[assinatura] = vf
        return vf

    def v_mais_proximo_do_ponto(self, ponto, amostras=300):
        melhor_v = 0.0
        melhor_d = float("inf")

        for i in range(amostras + 1):
            v = i / amostras
            p = self.centro_linha(v)
            d = self.distancia(p, ponto)
            if d < melhor_d:
                melhor_d = d
                melhor_v = v

        return melhor_v

    def construir_perfil_perspectiva(self, amostras=600):
        """Cria um eixo longitudinal em metros.

        A calibração de 191 px é aplicada no entorno de v=0.5.
        A variação da escala ao longo da profundidade é obtida pelo ponto
        de fuga. Assim, a mesma distância real ocupa menos pixels no fundo.
        """
        if len(self.pontos) != 6:
            return [], []

        vf = self.estimar_ponto_de_fuga_v()

        # Se não houver ponto de fuga confiável, usamos um perfil suave
        # baseado no encontro das bordas. Isso evita quebrar a grade.
        if vf is None or vf <= 1.01:
            vf = 1.25

        # Para uma estrada que foge para o topo, o ponto de fuga normalmente
        # fica depois do limite superior da grade: v_fuga > 1.
        if vf <= 1.0:
            vf = 1.01

        # Função projectiva: z(v) = v/(vf-v).
        def zproj(v):
            denominador = vf - v
            if denominador < 1e-6:
                denominador = 1e-6
            return v / denominador

        v_ref = 0.5
        z_ref = zproj(v_ref)

        # Encontramos o v que está aproximadamente 191 px adiante do
        # centro, seguindo o eixo da estrada.
        alvo_px = self.pixels_por_metro_na_tela()

        baixo = v_ref
        alto = 1.0
        if self.comprimento_curva_centro(v_ref, alto) < alvo_px:
            v_um_metro = alto
        else:
            for _ in range(30):
                meio = (baixo + alto) / 2
                if self.comprimento_curva_centro(v_ref, meio) < alvo_px:
                    baixo = meio
                else:
                    alto = meio
            v_um_metro = (baixo + alto) / 2

        dz_1m = zproj(v_um_metro) - z_ref
        if dz_1m <= 1e-8:
            dz_1m = 1e-4

        # Metro longitudinal correspondente a cada v.
        def metro(v):
            return (zproj(v) - z_ref) / dz_1m

        valores = []

        # Queremos todas as posições de metro inteiro desde o topo até o
        # fundo. O perfil é monotônico, então a interpolação é estável.
        vs = [i / amostras for i in range(amostras + 1)]
        ms = [metro(v) for v in vs]

        m_min = ms[0]
        m_max = ms[-1]

        primeiro = math.ceil(m_min - 1e-9)
        ultimo = math.floor(m_max + 1e-9)

        for m in range(primeiro, ultimo + 1):
            alvo = float(m)
            lo = 0
            hi = len(ms) - 1

            while lo <= hi:
                meio = (lo + hi) // 2
                if ms[meio] < alvo:
                    lo = meio + 1
                else:
                    hi = meio - 1

            i = max(1, min(lo, len(ms) - 1))
            m0, m1 = ms[i - 1], ms[i]
            v0, v1 = vs[i - 1], vs[i]

            if abs(m1 - m0) < 1e-12:
                v = v0
            else:
                fator = (alvo - m0) / (m1 - m0)
                v = v0 + fator * (v1 - v0)

            if 0.0 <= v <= 1.0:
                valores.append(v)

        # Sempre desenhamos as bordas.
        valores.extend([0.0, 1.0])

        valores = sorted(set(round(v, 8) for v in valores))

        # Se a grade tiver menos de dois metros, pelo menos mantém o meio.
        if len(valores) < 2:
            valores = [0.0, 1.0]

        return valores, vf

    def construir_colunas_1m(self):
        """Cria as posições laterais de 1 m na referência central.

        O número de metros inteiros que cabe na largura central é usado.
        Caso a largura não seja múltipla exata de 1 m, uma faixa parcial
        permanece nas bordas, sem alterar os 6 pontos de encaixe.
        """
        if len(self.pontos) != 6:
            return [0.0, 1.0]

        ppm = self.pixels_por_metro_na_tela()
        largura_centro = self.largura_nivel(0.5)

        largura_m = largura_centro / ppm
        quantidade = int(math.floor(largura_m + 1e-9))

        if quantidade < 1:
            return [0.0, 1.0]

        # Cada intervalo completo representa 1 m. A sobra é distribuída
        # somente como margem final para preservar exatamente os limites.
        valores = [0.0]
        for i in range(1, quantidade + 1):
            valores.append(i / quantidade)

        if valores[-1] < 1.0:
            valores.append(1.0)

        return valores

    def largura_central_em_metros(self):
        """Largura da faixa na linha central usando a calibração de 191 px/m."""
        if len(self.pontos) != 6:
            return 1.0

        ppm = self.pixels_por_metro_na_tela()
        if ppm <= 0:
            return 1.0

        return max(0.1, self.largura_nivel(0.5) / ppm)

    def ponto_offset_lateral(self, lado, metros, v):
        """Projeta o offset lateral com a proporção local da perspectiva.

        O offset representa uma distância física constante em metros, não
        uma fração fixa da largura medida no centro da grade. Como a largura
        aparente da faixa muda ao longo da imagem, calculamos a largura física
        local estimada em cada v e usamos essa largura para extrapolar a borda.
        Assim, o offset acompanha a perspectiva: maior em primeiro plano e
        menor ao longe, mantendo a proporção com a grade.

        lado=0 -> fora da borda esquerda
        lado=1 -> fora da borda direita
        """
        v = max(0.0, min(1.0, float(v)))
        largura_px_local = self.largura_nivel(v)
        ppm_local = self.escala_local_pixels_por_metro(v)
        largura_m_local = largura_px_local / max(1.0, ppm_local)

        # Proteção para grades degeneradas ou pontos muito próximos.
        largura_m_local = max(0.1, largura_m_local)
        fracao = max(0.0, float(metros)) / largura_m_local

        if lado == 0:
            return self.ponto_grade(-fracao, v)
        return self.ponto_grade(1.0 + fracao, v)

    def quantidade_offsets_laterais(self):
        """Quantidade de faixas de 1 m que cabem na área visível.

        Usamos a escala de 191 px/m como referência e deixamos algumas
        linhas extras para que o próprio QPainter recorte no limite da foto.
        """
        ppm = self.pixels_por_metro_na_tela()
        if ppm <= 0:
            return 1

        return max(1, min(20, math.ceil(max(self.width(), self.height()) / ppm) + 2))

    def gerar_pontos_grade_duplicada(self, direcao):
        """Gera uma cópia espelhada e contígua à grade principal.

        A borda interna da cópia coincide com a borda externa da grade
        principal. Os seis pontos da grade principal nunca são alterados.
        """
        if len(self.pontos) != 6 or direcao not in ("esquerda", "direita"):
            return []

        duplicados = []
        pares = ((0, 1), (2, 3), (4, 5))
        for indice_esq, indice_dir in pares:
            esquerda = self.pontos[indice_esq]
            direita = self.pontos[indice_dir]
            if direcao == "esquerda":
                # A borda direita da cópia coincide com a borda esquerda original.
                duplicados.extend([
                    QPointF(2 * esquerda.x() - direita.x(), esquerda.y()),
                    QPointF(esquerda.x(), esquerda.y()),
                ])
            else:
                # A borda esquerda da cópia coincide com a borda direita original.
                duplicados.extend([
                    QPointF(direita.x(), direita.y()),
                    QPointF(2 * direita.x() - esquerda.x(), direita.y()),
                ])

        # A ordem esperada por self.pontos é topo L/R, meio L/R, fundo L/R.
        return [duplicados[0], duplicados[1], duplicados[2], duplicados[3], duplicados[4], duplicados[5]]

    def desenhar_offsets_laterais(self, painter, linhas_v, lados=(0, 1)):
        """Desenha os offsets laterais de 1 em 1 metro, em tracejado.

        São criadas linhas longitudinais fora das duas bordas e também
        linhas transversais nos mesmos níveis métricos da grade, formando
        uma malha lateral para identificação de objetos e severidade.
        """
        if len(self.pontos) != 6:
            return

        quantidade = self.quantidade_offsets_laterais()
        pontos_amostragem = 160

        pen_offset = QPen(Qt.cyan, 2, Qt.DashLine)
        painter.setPen(pen_offset)

        # Linhas longitudinais: 1 m, 2 m, 3 m... apenas nos lados solicitados.
        for metros in range(1, quantidade + 1):
            for lado in lados:
                pontos = [
                    self.ponto_offset_lateral(lado, metros, i / pontos_amostragem)
                    for i in range(pontos_amostragem + 1)
                ]
                self.desenhar_linha_grade(painter, pontos)

        # Linhas transversais tracejadas nas posições métricas da grade,
        # estendidas para as áreas laterais. Elas fecham a referência de
        # distância lateral sem alterar a grade principal.
        for v in linhas_v:
            pontos = []

            if 0 in lados:
                # Da extremidade esquerda externa até a borda esquerda.
                p_externo_esq = self.ponto_offset_lateral(0, quantidade, v)
                p_borda_esq = self.curva_lateral(0, v)
                self.desenhar_linha_grade(painter, [p_externo_esq, p_borda_esq])

            if 1 in lados:
                # Da borda direita até a extremidade direita externa.
                p_borda_dir = self.curva_lateral(1, v)
                p_externo_dir = self.ponto_offset_lateral(1, quantidade, v)
                self.desenhar_linha_grade(painter, [p_borda_dir, p_externo_dir])

    def geometria_grade_metrica(self):
        assinatura = self.assinatura_grade()
        if assinatura is None:
            return [0.0, 1.0], [0.0, 1.0]

        # A grade principal e a duplicada alternam no mesmo paintEvent.
        # Cachear apenas a última assinatura fazia recalcular centenas de
        # amostras a cada quadro ao alternar entre elas.
        geometria = self._cache_geometrias.get(assinatura)
        if geometria is None:
            colunas_u = self.construir_colunas_1m()
            linhas_v, _ = self.construir_perfil_perspectiva()
            geometria = (colunas_u, linhas_v)
            if len(self._cache_geometrias) >= 4:
                self._cache_geometrias.clear()
            self._cache_geometrias[assinatura] = geometria

        self._cache_assinatura = assinatura
        self._cache_colunas_u, self._cache_linhas_v = geometria
        return geometria

    # ------------------------------------------------------------------
    # REDIMENSIONAMENTO DA ÁREA
    # ------------------------------------------------------------------
    def resizeEvent(self, evento):
        """Mantém a grade proporcional quando a janela é aumentada/diminuída."""
        novo_tamanho = self.size()
        anterior = self._ultimo_tamanho

        if (
            anterior is not None
            and anterior.width() > 0
            and anterior.height() > 0
            and len(self.pontos) == 6
            and not self._redimensionando_pontos
        ):
            sx = novo_tamanho.width() / anterior.width()
            sy = novo_tamanho.height() / anterior.height()

            self._redimensionando_pontos = True
            try:
                self.pontos = [
                    QPointF(p.x() * sx, p.y() * sy)
                    for p in self.pontos
                ]
                if len(self.pontos_grade_duplicada or []) == 6:
                    self.pontos_grade_duplicada = [
                        QPointF(p.x() * sx, p.y() * sy)
                        for p in self.pontos_grade_duplicada
                    ]
                self.atualizar_referencias_pontos()
                self.invalidar_cache_grade()
            finally:
                self._redimensionando_pontos = False

        self._ultimo_tamanho = novo_tamanho
        super().resizeEvent(evento)

    # ------------------------------------------------------------------
    # DESENHO
    # ------------------------------------------------------------------
    def desenhar_curva(self, painter, lado, largura=2):
        pen = QPen(Qt.white, largura)
        painter.setPen(pen)

        pontos = [
            self.curva_lateral(lado, i / 120)
            for i in range(121)
        ]

        for i in range(len(pontos) - 1):
            painter.drawLine(pontos[i], pontos[i + 1])

    def desenhar_linha_grade(self, painter, pontos):
        for i in range(len(pontos) - 1):
            painter.drawLine(pontos[i], pontos[i + 1])

    def _desenhar_tabela_overlay(self, painter):
        """Tabela de faixas + registro independente dos dois acostamentos."""
        dados = self.tabela_desenho.get(self.caminho_foto_atual, {}) if self.caminho_foto_atual else {}
        if not dados:
            return

        linhas = [["Faixa", "Largura", "Acost. direita", "Acost. esquerda"]]
        for chave in ("Faixa 1", "Faixa 2"):
            if chave == "Faixa 2" and not self._duas_faixas_ativas():
                continue
            if chave not in dados:
                continue
            largura = dados.get(chave, {}).get("largura")
            texto_largura = f"{largura:.2f} m" if isinstance(largura, (int, float)) else "—"
            linhas.append([chave, texto_largura, "—", "—"])

        acost = dados.get("_acostamentos", {})
        def exibir_medida(valor):
            if isinstance(valor, (int, float)):
                return f"{valor:.2f} m"
            if isinstance(valor, str) and valor:
                return valor
            return "—"

        # Os acostamentos pertencem aos extremos da via e não à Faixa 1/2.
        linhas.append([
            "Acostamentos", "—",
            exibir_medida(acost.get("direita")),
            exibir_medida(acost.get("esquerda"))
        ])

        larguras = [75, 78, 98, 98]
        altura_linha = 25
        largura_total = sum(larguras)
        x = self.width() - largura_total - 12
        y = 12
        altura_total = altura_linha * len(linhas)
        painter.setPen(QPen(Qt.black, 1))
        painter.setBrush(QBrush(Qt.white))
        painter.drawRect(x, y, largura_total, altura_total)

        for ri, linha in enumerate(linhas):
            xx = x
            for ci, texto in enumerate(linha):
                painter.drawText(xx + 2, y + ri * altura_linha, larguras[ci] - 4,
                                 altura_linha, Qt.AlignCenter, texto)
                xx += larguras[ci]
            if ri > 0:
                painter.drawLine(x, y + ri * altura_linha, x + largura_total, y + ri * altura_linha)
        xx = x
        for largura in larguras[:-1]:
            xx += largura
            painter.drawLine(xx, y, xx, y + altura_total)

    def _posicao_y_instrucao_desenho(self):
        dados = self.tabela_desenho.get(self.caminho_foto_atual, {}) if self.caminho_foto_atual else {}
        quantidade_faixas = 1 + int(self._duas_faixas_ativas())
        quantidade_linhas = 1 + quantidade_faixas + 1 if dados else 1
        return 12 + quantidade_linhas * 25 + 8

    def _desenhar_prompt_desenho(self, painter):
        self._rect_botoes_acostamento = {}
        if not (self.modo_desenho and self.prompt_desenho):
            return
        fm = painter.fontMetrics()
        caixa = fm.boundingRect(self.prompt_desenho).adjusted(-10, -6, 10, 6)
        caixa.moveTopRight(self.rect().topRight() + QPointF(-12, self._posicao_y_instrucao_desenho()).toPoint())
        painter.setPen(QPen(Qt.black, 1))
        painter.setBrush(QBrush(Qt.white))
        painter.drawRoundedRect(caixa, 6, 6)
        painter.setPen(QPen(Qt.black, 1))
        painter.drawText(caixa, Qt.AlignCenter, self.prompt_desenho)

        etapas = self._etapas_desenho()
        if not (0 <= self.etapa_desenho < len(etapas)):
            return
        if etapas[self.etapa_desenho].get("acao") != "decisao_acostamento":
            return
        largura_btn, altura_btn, espacamento = 64, 25, 6
        topo = caixa.bottom() + 5
        x_nao = caixa.right() - largura_btn
        x_sim = x_nao - espacamento - largura_btn
        self._rect_botoes_acostamento = {
            "sim": (x_sim, topo, largura_btn, altura_btn),
            "nao": (x_nao, topo, largura_btn, altura_btn),
        }
        for chave, texto in (("sim", "Sim"), ("nao", "Não")):
            x, y, w, h = self._rect_botoes_acostamento[chave]
            painter.setPen(QPen(Qt.black, 1))
            painter.setBrush(QBrush(Qt.white))
            painter.drawRoundedRect(x, y, w, h, 4, 4)
            painter.drawText(x, y, w, h, Qt.AlignCenter, texto)

    def paintEvent(self, evento):
        self._rect_botoes_acostamento = {}
        painter = QPainter(self)

        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.fillRect(self.rect(), Qt.lightGray)

            if self.imagem is None:
                painter.setPen(Qt.black)
                painter.drawText(
                    self.rect(),
                    Qt.AlignCenter,
                    "Nenhuma imagem carregada"
                )
                return

            painter.setRenderHint(
                QPainter.SmoothPixmapTransform,
                True
            )

            painter.drawPixmap(
                0, 0,
                self.width(),
                self.height(),
                self.imagem
            )

            # Os desenhos salvos pertencem à foto, não ao estado do botão.
            # Desativar Desenho ou ocultar a grade nunca apaga nem oculta linhas
            # e tabela já registradas; somente "Excluir desenho" as remove.
            caminho_foto = self.caminho_foto_atual
            linhas_salvas = (
                self.desenhos_por_foto.get(caminho_foto, [])
                if caminho_foto else []
            )
            dados_tabela = (
                self.tabela_desenho.get(caminho_foto, {})
                if caminho_foto else {}
            )
            tabela_tem_valores = any(
                valor is not None and valor != ""
                for grupo in dados_tabela.values()
                if isinstance(grupo, dict)
                for valor in grupo.values()
            )
            if self.modo_desenho or linhas_salvas or tabela_tem_valores:
                cores_desenho = {
                    "verde": Qt.green, "amarelo": Qt.yellow,
                    "vermelho": Qt.red, "magenta": Qt.magenta
                }
                for item in linhas_salvas:
                    pts = [
                        QPointF(x * self.width(), y * self.height())
                        for x, y in item.get("pontos", [])
                    ]
                    cor = cores_desenho.get(item.get("cor"), Qt.green)
                    painter.setPen(QPen(cor, 3))
                    self.desenhar_linha_grade(painter, pts)
                    valor = item.get("metros")
                    if (len(pts) >= 2 and isinstance(valor, (int, float))
                            and math.isfinite(float(valor))):
                        meio = QPointF(
                            (pts[0].x() + pts[-1].x()) / 2.0,
                            (pts[0].y() + pts[-1].y()) / 2.0
                        )
                        texto = f"{float(valor):.2f} m"
                        caixa = painter.fontMetrics().boundingRect(texto).adjusted(-7, -4, 7, 4)
                        caixa.moveCenter(meio.toPoint())
                        painter.setPen(QPen(Qt.black, 1))
                        painter.setBrush(QBrush(cor))
                        painter.drawRoundedRect(caixa, 5, 5)
                        painter.setPen(QPen(Qt.white, 1))
                        painter.drawText(caixa, Qt.AlignCenter, texto)
                self._desenhar_tabela_overlay(painter)

            if not self.mostrar_grade or len(self.pontos) != 6:
                self._desenhar_prompt_desenho(painter)
                return

            # Desenha a grade principal com o mesmo cálculo de antes.
            colunas_u, linhas_v = self.geometria_grade_metrica()
            pen_grade = QPen(Qt.white, 2)
            painter.setPen(pen_grade)
            for u in colunas_u:
                pontos = [self.ponto_grade(u, i / 120) for i in range(121)]
                self.desenhar_linha_grade(painter, pontos)
            for v in linhas_v:
                pontos = [self.ponto_grade(i / 120, v) for i in range(121)]
                self.desenhar_linha_grade(painter, pontos)

            # Com duas grades, cada uma mantém somente o offset voltado
            # para fora da pista; o offset interno entre elas é omitido.
            if self.mostrar_offset_lateral:
                if self.direcao_grade_duplicada == "esquerda":
                    lados_principal = (1,)
                elif self.direcao_grade_duplicada == "direita":
                    lados_principal = (0,)
                else:
                    lados_principal = (0, 1)
                self.desenhar_offsets_laterais(painter, linhas_v, lados_principal)

            # Bordas superiores/inferiores e laterais curvas da principal.
            pen_borda = QPen(Qt.white, 3)
            painter.setPen(pen_borda)
            painter.drawLine(self.pontos[0], self.pontos[1])
            painter.drawLine(self.pontos[4], self.pontos[5])
            self.desenhar_curva(painter, 0, 3)
            self.desenhar_curva(painter, 1, 3)

            # Segunda grade: geometria espelhada, derivada da principal.
            # O estado original é restaurado imediatamente após o desenho.
            if self.direcao_grade_duplicada in ("esquerda", "direita"):
                pontos_principais = self.pontos
                pontos_duplicados = self.pontos_grade_duplicada
                if len(pontos_duplicados or []) == 6:
                    self.pontos = pontos_duplicados
                    colunas_dup, linhas_dup = self.geometria_grade_metrica()
                    painter.setPen(QPen(Qt.white, 2))
                    for u in colunas_dup:
                        pontos = [self.ponto_grade(u, i / 120) for i in range(121)]
                        self.desenhar_linha_grade(painter, pontos)
                    for v in linhas_dup:
                        pontos = [self.ponto_grade(i / 120, v) for i in range(121)]
                        self.desenhar_linha_grade(painter, pontos)
                    painter.setPen(QPen(Qt.white, 3))
                    painter.drawLine(self.pontos[0], self.pontos[1])
                    painter.drawLine(self.pontos[4], self.pontos[5])
                    self.desenhar_curva(painter, 0, 3)
                    self.desenhar_curva(painter, 1, 3)
                    if self.mostrar_offset_lateral:
                        # Com grade duplicada, desenhar somente os offsets externos.
                        # Os offsets internos sobrepostos criavam cruzamentos e
                        # confundiam a seleção do acostamento.
                        lados_dup = (0,) if self.direcao_grade_duplicada == "esquerda" else (1,)
                        self.desenhar_offsets_laterais(painter, linhas_dup, lados_dup)
                    self.pontos = pontos_principais
                    self.atualizar_referencias_pontos()
                    # Recalcula/recupera a geometria da grade principal para
                    # que a seleção e as medições existentes permaneçam intactas.
                    colunas_u, linhas_v = self.geometria_grade_metrica()

            # Destaque da função Linha de bordo nas grades certas.
            if self.modo_linha_bordo or self.bordos_selecionados:
                principais = self.pontos
                duplicados = self.pontos_grade_duplicada
                try:
                    selecionados = set(self.bordos_selecionados)
                    if self.lado_bordo_selecionado is not None:
                        selecionados.add((self.grade_bordo_selecionada, self.lado_bordo_selecionado))
                    for nome_grade, lado in selecionados:
                        if nome_grade == "duplicada":
                            if len(duplicados or []) != 6:
                                continue
                            self.pontos = duplicados
                        else:
                            self.pontos = principais
                        pts_bordo = [self.curva_lateral(lado, i / 160) for i in range(161)]
                        painter.setPen(QPen(Qt.yellow, 4))
                        self.desenhar_linha_grade(painter, pts_bordo)
                finally:
                    self.pontos = principais
                    self.atualizar_referencias_pontos()

            if self.ponto_externo_bordo is not None and self.ponto_inicio_bordo is not None:
                p1 = self.ponto_inicio_bordo
                p2 = self.ponto_externo_bordo
                painter.setPen(QPen(Qt.red, 4))
                painter.drawLine(p1, p2)
                painter.setPen(QPen(Qt.red, 1))
                painter.setBrush(QBrush(Qt.red))
                painter.drawEllipse(p1, 5, 5)
                painter.drawEllipse(p2, 5, 5)
                if self.medida_bordo_metros is not None:
                    meio = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
                    texto = f"{self.medida_bordo_metros:.2f} m"
                    caixa = painter.fontMetrics().boundingRect(texto)
                    caixa.moveCenter(meio.toPoint())
                    caixa.adjust(-8, -5, 8, 5)
                    painter.setPen(QPen(Qt.red, 1))
                    painter.setBrush(QBrush(Qt.red))
                    painter.drawRoundedRect(caixa, 5, 5)
                    painter.setPen(QPen(Qt.white, 1))
                    painter.drawText(caixa, Qt.AlignCenter, texto)

            # Alças.
            for ponto in self.pontos:
                painter.setPen(QPen(Qt.white, 2))
                painter.setBrush(QBrush(Qt.green))
                painter.drawEllipse(
                    ponto,
                    self.raio_alca,
                    self.raio_alca
                )

                painter.setBrush(QBrush(Qt.white))
                painter.drawEllipse(ponto, 3, 3)

            # As seis alças da segunda grade também ficam visíveis e manipuláveis.
            if self.direcao_grade_duplicada in ("esquerda", "direita") and len(self.pontos_grade_duplicada or []) == 6:
                for ponto in self.pontos_grade_duplicada:
                    painter.setPen(QPen(Qt.white, 2))
                    painter.setBrush(QBrush(Qt.green))
                    painter.drawEllipse(ponto, self.raio_alca, self.raio_alca)

            # A instrução do modo Desenho fica sempre visível, inclusive com a grade ativa.
            self._desenhar_prompt_desenho(painter)

            # Medição: linha de destaque e valor em metros na grade escolhida.
            if self.modo_medicao and self.pontos_medicao:
                pontos_grade_medicao = (
                    self.pontos_grade_duplicada
                    if self.grade_medicao_selecionada == "duplicada"
                    else self.pontos
                )
                if len(pontos_grade_medicao or []) == 6:
                    pen_medicao = QPen(Qt.green, 4)
                    painter.setPen(pen_medicao)

                    if len(self.pontos_medicao) == 1:
                        p = pontos_grade_medicao[self.pontos_medicao[0]]
                        painter.setBrush(QBrush(Qt.green))
                        painter.drawEllipse(p, self.raio_alca + 3, self.raio_alca + 3)
                    elif len(self.pontos_medicao) == 2:
                        i, j = self.pontos_medicao
                        p1 = pontos_grade_medicao[i]
                        p2 = pontos_grade_medicao[j]
                        painter.drawLine(p1, p2)

                        # Marcadores maiores nos dois pontos escolhidos.
                        painter.setBrush(QBrush(Qt.green))
                        painter.drawEllipse(p1, self.raio_alca + 2, self.raio_alca + 2)
                        painter.drawEllipse(p2, self.raio_alca + 2, self.raio_alca + 2)

                        meio = QPointF(
                            (p1.x() + p2.x()) / 2,
                            (p1.y() + p2.y()) / 2
                        )
                        texto = f"{self.medida_metros:.2f} m"
                        caixa = painter.fontMetrics().boundingRect(texto)
                        caixa.moveCenter(meio.toPoint())
                        caixa.adjust(-8, -5, 8, 5)
                        painter.setPen(QPen(Qt.black, 1))
                        painter.setBrush(QBrush(Qt.green))
                        painter.drawRoundedRect(caixa, 5, 5)
                        painter.setPen(Qt.black)
                        painter.drawText(caixa, Qt.AlignCenter, texto)

        finally:
            painter.end()

    # ------------------------------------------------------------------
    # INTERAÇÃO DO MOUSE
    # ------------------------------------------------------------------
    def encontrar_alca(self, posicao):
        for i, ponto in enumerate(self.pontos):
            if self.distancia(posicao, ponto) <= self.raio_alca + 8:
                return i
        return -1

    def ponto_dentro_grade(self, ponto):
        if len(self.pontos) != 6:
            return False

        poligono = self.pontos_contorno()
        if not poligono:
            return False

        dentro = False
        j = len(poligono) - 1

        for i in range(len(poligono)):
            xi = poligono[i].x()
            yi = poligono[i].y()
            xj = poligono[j].x()
            yj = poligono[j].y()

            if (yi > ponto.y()) != (yj > ponto.y()):
                x_intersecao = (
                    (xj - xi) *
                    (ponto.y() - yi) /
                    (yj - yi) + xi
                )

                if ponto.x() < x_intersecao:
                    dentro = not dentro

            j = i

        return dentro

    def pontos_contorno(self, amostras=80):
        if len(self.pontos) != 6:
            return []

        pontos = [self.pontos[0], self.pontos[1]]

        for i in range(1, amostras + 1):
            pontos.append(
                self.curva_lateral(1, i / amostras)
            )

        pontos.append(self.pontos[4])

        for i in range(1, amostras + 1):
            pontos.append(
                self.curva_lateral(0, 1 - i / amostras)
            )

        return pontos

    def _selecionar_grade_mouse(self, posicao):
        """Decide se o clique pertence à segunda grade (alças ou interior)."""
        if (self.direcao_grade_duplicada not in ("esquerda", "direita")
                or len(self.pontos_grade_duplicada or []) != 6
                or self.modo_linha_bordo):
            return False

        # Prioriza as seis alças da segunda grade. Isso também permite
        # selecionar os pontos da grade duplicada no modo Medir.
        for ponto in self.pontos_grade_duplicada:
            if self.distancia(posicao, ponto) <= self.raio_alca + 8:
                return True

        # No modo Medir, somente as alças selecionam pontos; clicar no
        # interior não deve iniciar o arraste da grade.
        if self.modo_medicao:
            return False

        # Também permite arrastar a segunda grade clicando no seu interior.
        pontos_principais = self.pontos
        try:
            self.pontos = self.pontos_grade_duplicada
            return self.ponto_dentro_grade(posicao)
        finally:
            self.pontos = pontos_principais
            self.atualizar_referencias_pontos()

    def _com_grade_mouse_ativa(self, funcao, evento, finalizar=False):
        """Executa o evento sobre a grade selecionada e restaura a principal."""
        if not self._grade_mouse_duplicada:
            funcao(evento)
            return

        pontos_principais = self.pontos
        try:
            self.pontos = self.pontos_grade_duplicada
            self.atualizar_referencias_pontos()
            funcao(evento)
            self.pontos_grade_duplicada = self.pontos
        finally:
            self.pontos = pontos_principais
            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()
            self.update()
            if finalizar:
                self._grade_mouse_duplicada = False

    def mousePressEvent(self, evento):
        if evento.button() == Qt.LeftButton and self.modo_desenho:
            # No modo Desenho o clique é sempre uma seleção de linha, nunca
            # um arraste/seleção de grade; as rotinas trocam de grade explicitamente.
            self._grade_mouse_duplicada = False
            self._mousePressEvent_impl(evento)
            return
        if evento.button() == Qt.LeftButton:
            self._grade_mouse_duplicada = self._selecionar_grade_mouse(evento.position())
        self._com_grade_mouse_ativa(self._mousePressEvent_impl, evento)

    def mouseMoveEvent(self, evento):
        self._com_grade_mouse_ativa(self._mouseMoveEvent_impl, evento)

    def mouseReleaseEvent(self, evento):
        self._com_grade_mouse_ativa(self._mouseReleaseEvent_impl, evento, finalizar=True)

    def _mousePressEvent_impl(self, evento):
        if evento.button() != Qt.LeftButton:
            return

        posicao = evento.position()
        alca = self.encontrar_alca(posicao)


        if self.modo_desenho:
            if not self.caminho_foto_atual:
                self.prompt_desenho = "Desenho: selecione uma pasta e carregue uma foto primeiro."
                self.update()
                return

            etapas = self._etapas_desenho()
            if self.etapa_desenho >= len(etapas) or etapas[self.etapa_desenho].get("acao") == "fim":
                self.prompt_desenho = "Desenho concluído. Use Excluir desenho para refazer esta foto."
                self.update()
                return

            etapa = etapas[self.etapa_desenho]
            acao = etapa["acao"]

            if acao == "decisao_acostamento":
                escolhido = None
                x_click, y_click = posicao.x(), posicao.y()
                for opcao, rect in self._rect_botoes_acostamento.items():
                    x, y, w, h = rect
                    if x <= x_click <= x + w and y <= y_click <= y + h:
                        escolhido = opcao
                        break
                if escolhido is None:
                    return
                nome_lado = etapa["nome_lado"]
                tabela = self.tabela_desenho.setdefault(self.caminho_foto_atual, {})
                acostamentos = tabela.setdefault("_acostamentos", {"direita": None, "esquerda": None})
                if escolhido == "nao":
                    acostamentos[nome_lado] = "Não possui"
                    self._lado_desenho_temporario = None
                    self.selecoes_temporarias_desenho[self.caminho_foto_atual] = None
                    self.etapa_desenho += 3
                else:
                    acostamentos[nome_lado] = None
                    self.etapa_desenho += 1
                self.etapas_desenho_por_foto[self.caminho_foto_atual] = self.etapa_desenho
                etapas_novas = self._etapas_desenho()
                self.prompt_desenho = (
                    etapas_novas[self.etapa_desenho]["prompt"]
                    if self.etapa_desenho < len(etapas_novas)
                    else "Desenho concluído. As linhas e medidas desta foto foram salvas."
                )
                self._salvar_estado_desenho()
                self.update()
                return

            grade_nome = etapa.get("grade", "principal")
            faixa = etapa.get("faixa", "Faixa 1")
            lado = etapa.get("lado")
            nome_lado = etapa.get("nome_lado", "")
            tipo = etapa.get("tipo", "bordo")

            if acao == "linha":
                tipo_busca = "centro" if tipo == "centro" else "bordo"
                achou = self._achar_bordo_desenho(
                    posicao, grade_nome, tipo_busca, lado_forcado=lado
                )
                if achou[0] > 42:
                    return

                selecao = {
                    "grade": grade_nome, "faixa": faixa, "tipo": tipo,
                    "lado": achou[1], "v": achou[2], "ponto": achou[3]
                }
                anterior = self._lado_desenho_temporario

                # Cada linha só é registrada depois deste clique válido.
                self._registrar_linha_desenho(
                    tipo, grade_nome, achou[1], achou[2], etapa["cor"],
                    faixa=faixa, papel=etapa.get("papel")
                )

                if etapa.get("finaliza_largura"):
                    if anterior is None or anterior.get("faixa") != faixa:
                        # Falha segura: não inventa medida sem o primeiro elemento.
                        self.desenhos_por_foto[self.caminho_foto_atual].pop()
                        self.prompt_desenho = "Selecione primeiro a linha inicial indicada pela instrução."
                        self._salvar_estado_desenho()
                        self.update()
                        return
                    metros = self._medir_largura_desenho(
                        anterior, selecao, etapa["finaliza_largura"]
                    )
                    if isinstance(metros, (int, float)) and math.isfinite(float(metros)):
                        p1_cota, p2_cota = self._pontos_segmento_largura_desenho(anterior, selecao)
                        if p1_cota is not None and p2_cota is not None:
                            self._registrar_segmento_medicao(
                                p1_cota, p2_cota, metros, "medida_largura", faixa,
                                f"largura_{faixa.lower().replace(' ', '_')}", cor="magenta"
                            )
                    tabela = self.tabela_desenho.setdefault(self.caminho_foto_atual, {})
                    valores = tabela.setdefault(faixa, {
                        "largura": None, "acostamento_direita": None, "acostamento_esquerda": None
                    })
                    valores["largura"] = metros
                    self._lado_desenho_temporario = None
                    self.selecoes_temporarias_desenho[self.caminho_foto_atual] = None
                else:
                    self._lado_desenho_temporario = selecao
                    self.selecoes_temporarias_desenho[self.caminho_foto_atual] = selecao
                    self._bordos_desenho_por_faixa[faixa] = achou[1]

                self.etapa_desenho += 1
                etapas_novas = self._etapas_desenho()
                if self.etapa_desenho < len(etapas_novas):
                    self.prompt_desenho = etapas_novas[self.etapa_desenho]["prompt"]
                else:
                    self.prompt_desenho = "Desenho concluído. As linhas e medidas desta foto foram salvas."
                self._salvar_estado_desenho()
                self.update()
                return

            if acao == "selecionar_bordo_acostamento":
                achou = self._achar_bordo_desenho(
                    posicao, grade_nome, "bordo", lado_forcado=lado
                )
                if achou[0] > 42:
                    return
                # É uma seleção nova do bordo para o acostamento; não cria
                # outra linha verde duplicada, apenas registra grade e lado.
                self._lado_desenho_temporario = {
                    "grade": grade_nome, "faixa": faixa, "lado": lado,
                    "v": achou[2], "ponto": achou[3], "papel": "acostamento"
                }
                self.selecoes_temporarias_desenho[self.caminho_foto_atual] = self._lado_desenho_temporario
                self.etapa_desenho += 1
                etapas_novas = self._etapas_desenho()
                self.prompt_desenho = etapas_novas[self.etapa_desenho]["prompt"]
                self._salvar_estado_desenho()
                self.update()
                return

            if acao == "medir_acostamento":
                if not self.mostrar_offset_lateral:
                    self.prompt_desenho = (
                        f"Desenho: ative Offset lateral para medir o acostamento {nome_lado} da {faixa}."
                    )
                    self.update()
                    return
                selecao = self._lado_desenho_temporario
                if (not selecao or selecao.get("grade") != grade_nome
                        or selecao.get("faixa") != faixa or selecao.get("lado") != lado):
                    self.prompt_desenho = (
                        f"Desenho: selecione novamente a linha de bordo do lado {nome_lado} da {faixa}."
                    )
                    self.update()
                    return
                metros, ponto_bordo, ponto_offset = self._medir_offset_grade_desenho(
                    grade_nome, lado, posicao
                )
                if metros is None or ponto_bordo is None or ponto_offset is None:
                    self.prompt_desenho = (
                        f"Desenho: clique em qualquer ponto da área do offset lateral {nome_lado}; será usada a transversal mais próxima."
                    )
                    self.update()
                    return

                # Desenha a medição do acostamento sobre a transversal tracejada
                # existente: do bordo até o ponto projetado no offset. Não desenha
                # a linha longitudinal inteira do offset lateral.
                self._registrar_segmento_acostamento(
                    ponto_bordo, ponto_offset, grade_nome, lado, metros,
                    faixa, f"acostamento_{nome_lado}"
                )
                tabela = self.tabela_desenho.setdefault(self.caminho_foto_atual, {})
                acostamentos = tabela.setdefault("_acostamentos", {"direita": None, "esquerda": None})
                acostamentos[nome_lado] = metros
                self._lado_desenho_temporario = None
                self.selecoes_temporarias_desenho[self.caminho_foto_atual] = None
                self.etapa_desenho += 1
                self.etapas_desenho_por_foto[self.caminho_foto_atual] = self.etapa_desenho
                etapas_novas = self._etapas_desenho()
                self.prompt_desenho = (
                    etapas_novas[self.etapa_desenho]["prompt"]
                    if self.etapa_desenho < len(etapas_novas)
                    else "Desenho concluído. As linhas e medidas desta foto foram salvas."
                )
                self._salvar_estado_desenho()
                self.update()
                return
            return

        # O modo Linha de bordo reconhece apenas os bordos externos válidos
        # das grades atualmente ativas e associa cada bordo ao seu offset.
        if self.modo_linha_bordo:
            lado_detectado = self.detectar_linha_bordo(posicao)
            if self.lado_bordo_selecionado is None:
                if lado_detectado is not None:
                    self.grade_bordo_selecionada, self.lado_bordo_selecionado = lado_detectado
                    self.bordos_selecionados = {lado_detectado}
                    self.ponto_externo_bordo = None
                    self.ponto_inicio_bordo = None
                    self.medida_bordo_metros = None
                    self.update()
                return

            selecionado = (self.grade_bordo_selecionada, self.lado_bordo_selecionado)

            # A função Linha de bordo aceita somente um bordo por vez:
            # primeiro seleciona o bordo externo e depois um ponto no offset.
            # Clicar em outro bordo não calcula largura nem troca a seleção.
            if lado_detectado is not None:
                if lado_detectado == selecionado:
                    # Clicar novamente no mesmo bordo limpa a medição atual.
                    self.ponto_externo_bordo = None
                    self.ponto_inicio_bordo = None
                    self.medida_bordo_metros = None
                    self.bordos_selecionados = {selecionado}
                    self.update()
                return

            # Segundo clique no offset externo correspondente ao bordo escolhido.
            principais = self.pontos
            duplicados = self.pontos_grade_duplicada
            try:
                self.pontos = duplicados if self.grade_bordo_selecionada == "duplicada" else principais
                self.invalidar_cache_grade()
                metros, ponto_bordo, ponto_externo_alinhado = self.medir_bordo_ate_ponto(
                    self.lado_bordo_selecionado, posicao
                )
            finally:
                self.pontos = principais
                self.atualizar_referencias_pontos()
                self.invalidar_cache_grade()

            if metros is not None and ponto_bordo is not None and ponto_externo_alinhado is not None:
                self.ponto_inicio_bordo = ponto_bordo
                self.ponto_externo_bordo = ponto_externo_alinhado
                self.medida_bordo_metros = metros
                self.bordos_selecionados = {selecionado}
                self.update()
            return

        if self.modo_medicao:
            if alca != -1:
                grade_clicada = (
                    "duplicada" if self._grade_mouse_duplicada else "principal"
                )

                # Não misturamos pontos de grades diferentes na mesma medida.
                # Se o usuário mudar de grade, o clique inicia uma nova medida.
                if (self.pontos_medicao
                        and grade_clicada != self.grade_medicao_selecionada):
                    self.pontos_medicao = []
                    self.medida_metros = None

                if not self.pontos_medicao:
                    self.grade_medicao_selecionada = grade_clicada
                    self.pontos_medicao = [alca]
                    self.medida_metros = None
                elif len(self.pontos_medicao) == 1:
                    if alca == self.pontos_medicao[0]:
                        self.pontos_medicao = []
                        self.medida_metros = None
                    else:
                        self.pontos_medicao.append(alca)
                        self.medida_metros = self.distancia_medida_em_metros(
                            self.pontos_medicao[0],
                            self.pontos_medicao[1]
                        )
                else:
                    self.grade_medicao_selecionada = grade_clicada
                    self.pontos_medicao = [alca]
                    self.medida_metros = None
                self.update()
            return

        if alca != -1:
            self.alca_selecionada = alca
            return

        if self.mostrar_grade and self.ponto_dentro_grade(posicao):
            self.movendo_grade = True
            self.ultima_posicao_mouse = posicao

    def _mouseMoveEvent_impl(self, evento):
        posicao = evento.position()

        if self.alca_selecionada != -1:
            indice = self.alca_selecionada

            if indice in (0, 1):
                esquerda, direita = 0, 1
            elif indice in (2, 3):
                esquerda, direita = 2, 3
            else:
                esquerda, direita = 4, 5

            # Mantém exatamente o comportamento da versão que estava
            # correta para o encaixe: só o par do mesmo nível acompanha
            # no eixo Y. Os outros níveis não são alterados.
            self.pontos[indice] = QPointF(
                posicao.x(),
                posicao.y()
            )

            outro = direita if indice == esquerda else esquerda
            self.pontos[outro] = QPointF(
                self.pontos[outro].x(),
                posicao.y()
            )

            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()
            self.update()
            return

        if self.movendo_grade:
            dx = posicao.x() - self.ultima_posicao_mouse.x()
            dy = posicao.y() - self.ultima_posicao_mouse.y()

            for i in range(6):
                self.pontos[i] = QPointF(
                    self.pontos[i].x() + dx,
                    self.pontos[i].y() + dy
                )

            self.atualizar_referencias_pontos()
            self.invalidar_cache_grade()
            self.ultima_posicao_mouse = posicao
            self.update()

    def _mouseReleaseEvent_impl(self, evento):
        if evento.button() != Qt.LeftButton:
            return

        self.alca_selecionada = -1
        self.movendo_grade = False


class SistemaRodovia(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Sistema de Análise de Rodovia")
        self.resize(1200, 760)
        self.setMinimumSize(800, 500)

        self._tamanho_inicial_definido = False

        self.lista_imagens = []
        self.indice_imagem = -1

        self.botao_pasta = QPushButton("📁 Selecionar pasta")
        self.botao_pasta.clicked.connect(self.selecionar_pasta)

        self.botao_grade = QPushButton("☐ Mostrar grade")
        self.botao_grade.setCheckable(True)
        self.botao_grade.clicked.connect(self.alternar_grade)

        self.seletor_grade_duplicada = QComboBox()
        self.seletor_grade_duplicada.addItem("Sem duplicação", userData=None)
        self.seletor_grade_duplicada.addItem("Duplicar para esquerda", userData="esquerda")
        self.seletor_grade_duplicada.addItem("Duplicar para direita", userData="direita")
        self.seletor_grade_duplicada.setToolTip(
            "Cria uma cópia espelhada da grade principal no lado escolhido"
        )
        self.seletor_grade_duplicada.currentIndexChanged.connect(
            self.alternar_grade_duplicada
        )

        self.botao_medicao = QPushButton("✏️ Medir")
        self.botao_medicao.setCheckable(True)
        self.botao_medicao.setToolTip(
            "Clique no lápis e selecione dois dos seis pontos para medir a distância"
        )
        self.botao_medicao.clicked.connect(self.alternar_medicao)
        self.botao_medicao.hide()

        self.botao_desenho = QPushButton("✏️ Desenho")
        self.botao_desenho.setCheckable(True)
        self.botao_desenho.setToolTip("Modo guiado para desenhar linhas e preencher a tabela por foto")
        self.botao_desenho.clicked.connect(self.alternar_desenho)

        self.botao_excluir_desenho = QPushButton("Excluir desenho")
        self.botao_excluir_desenho.clicked.connect(self.excluir_desenho)

        self.botao_offset = QPushButton("↔ Offset lateral")
        self.botao_offset.setCheckable(True)
        self.botao_offset.setToolTip(
            "Mostra linhas laterais tracejadas em intervalos de 1 metro"
        )
        self.botao_offset.clicked.connect(self.alternar_offset)

        self.botao_linha_bordo = QPushButton("↔ Linha de bordo")
        self.botao_linha_bordo.setCheckable(True)
        self.botao_linha_bordo.setToolTip(
            "Selecione um bordo externo e depois clique no offset lateral correspondente para medir a distância."
        )
        self.botao_linha_bordo.clicked.connect(self.alternar_linha_bordo)
        self.botao_linha_bordo.hide()

        self.indicador = QLabel("Nenhuma pasta selecionada")
        self.indicador.setAlignment(Qt.AlignCenter)

        self.campo_foto = QLineEdit()
        self.campo_foto.setPlaceholderText("Ir para foto nº")
        self.campo_foto.setFixedWidth(150)
        self.campo_foto.setAlignment(Qt.AlignCenter)
        self.campo_foto.setToolTip(
            "Digite o número da foto e pressione Enter"
        )
        self.campo_foto.returnPressed.connect(self.ir_para_foto)

        self.area_imagem = AreaImagem()

        self.botao_anterior = QPushButton("◀", self.area_imagem)
        self.botao_proximo = QPushButton("▶", self.area_imagem)

        estilo_navegacao = """
            QPushButton {
                background-color: transparent;
                color: transparent;
                border: none;
                font-size: 32px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 90);
                color: white;
            }
            QPushButton:disabled {
                background-color: transparent;
                color: transparent;
            }
        """

        self.botao_anterior.setStyleSheet(estilo_navegacao)
        self.botao_proximo.setStyleSheet(estilo_navegacao)

        self.botao_anterior.setCursor(Qt.PointingHandCursor)
        self.botao_proximo.setCursor(Qt.PointingHandCursor)

        self.botao_anterior.setEnabled(False)
        self.botao_proximo.setEnabled(False)

        self.botao_anterior.clicked.connect(self.imagem_anterior)
        self.botao_proximo.clicked.connect(self.imagem_proxima)

        self.posicionar_botoes_navegacao()

        barra = QHBoxLayout()
        barra.setContentsMargins(0, 0, 0, 0)
        barra.addWidget(self.botao_pasta)
        barra.addStretch()
        barra.addWidget(self.indicador)
        barra.addStretch()
        barra.addWidget(self.campo_foto)
        barra.addSpacing(8)
        barra.addWidget(self.botao_grade)
        barra.addSpacing(6)
        barra.addWidget(self.seletor_grade_duplicada)
        barra.addSpacing(6)
        # Medir e Linha de bordo deixam de aparecer na interface.
        # Seus objetos e métodos são mantidos internamente para não quebrar
        # a lógica compartilhada pela função Desenho.
        barra.addWidget(self.botao_desenho)
        barra.addSpacing(6)
        barra.addWidget(self.botao_excluir_desenho)
        barra.addSpacing(6)
        barra.addWidget(self.botao_offset)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(barra)
        layout.addWidget(self.area_imagem)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

    def alternar_grade_duplicada(self, indice):
        """Ativa/desativa a segunda grade e mantém o fluxo Desenho coerente."""
        area = self.area_imagem
        nova_direcao = self.seletor_grade_duplicada.currentData()
        direcao_anterior = area.direcao_grade_duplicada
        if nova_direcao == direcao_anterior:
            return

        area.direcao_grade_duplicada = nova_direcao
        caminho = area.caminho_foto_atual

        if nova_direcao is None:
            # Desativar a grade duplicada muda a vista e esconde a linha Faixa 2
            # da tabela, mas não apaga desenhos nem valores já salvos.
            area.pontos_grade_duplicada = None
            area._direcao_pontos_grade_duplicada = None
        else:
            area.pontos_grade_duplicada = area.gerar_pontos_grade_duplicada(nova_direcao)
            area._direcao_pontos_grade_duplicada = nova_direcao
            if caminho:
                tabela = area.tabela_desenho.setdefault(caminho, {})
                tabela.setdefault("Faixa 2", {"largura": None})
                tabela.setdefault("_acostamentos", {"direita": None, "esquerda": None})

        if caminho:
            area.etapas_desenho_por_foto[caminho] = 0
            area.selecoes_temporarias_desenho[caminho] = None
        area.etapa_desenho = 0
        area._lado_desenho_temporario = None
        area._bordos_desenho_por_faixa = {}
        if area.modo_desenho:
            etapas = area._etapas_desenho()
            area.prompt_desenho = etapas[0]["prompt"] if etapas else ""
        if nova_direcao is not None and not area.mostrar_grade:
            self.botao_grade.setChecked(True)
            self.alternar_grade(True)
        area.invalidar_cache_grade()
        area._salvar_estado_desenho()
        area.update()

    def alternar_offset(self, ativada):
        self.botao_offset.setText("☑ Offset lateral" if ativada else "↔ Offset lateral")
        area = self.area_imagem
        area.alternar_offset_lateral(ativada)
        if area.modo_desenho and ativada:
            etapas = area._etapas_desenho()
            if area.etapa_desenho < len(etapas):
                area.prompt_desenho = etapas[area.etapa_desenho]["prompt"]
                area.update()

    def alternar_linha_bordo(self, ativada):
        if ativada:
            # Os modos Medir e Linha de bordo não podem disputar os cliques.
            if self.botao_medicao.isChecked():
                self.botao_medicao.setChecked(False)
                self.botao_medicao.setText("✏️ Medir")
                self.area_imagem.alternar_medicao(False)
            self.botao_linha_bordo.setText("☑ Linha de bordo")
        else:
            self.botao_linha_bordo.setText("↔ Linha de bordo")
        self.area_imagem.alternar_linha_bordo(ativada)

    def posicionar_botoes_navegacao(self):
        if not hasattr(self, "area_imagem"):
            return

        largura = self.area_imagem.width()
        altura = self.area_imagem.height()
        tamanho_botao = 72
        y = max(0, (altura - tamanho_botao) // 2)

        self.botao_anterior.setGeometry(
            0, y, tamanho_botao, tamanho_botao
        )
        self.botao_proximo.setGeometry(
            max(0, largura - tamanho_botao),
            y,
            tamanho_botao,
            tamanho_botao
        )

        self.botao_anterior.raise_()
        self.botao_proximo.raise_()

    def selecionar_pasta(self):
        pasta = QFileDialog.getExistingDirectory(
            self,
            "Selecionar pasta com imagens"
        )

        if not pasta:
            return

        extensoes = (
            ".png", ".jpg", ".jpeg",
            ".bmp", ".webp"
        )

        self.lista_imagens = []

        for nome in os.listdir(pasta):
            caminho = os.path.join(pasta, nome)
            if (
                os.path.isfile(caminho)
                and nome.lower().endswith(extensoes)
            ):
                self.lista_imagens.append(caminho)

        self.lista_imagens.sort(
            key=lambda caminho: os.path.basename(caminho).lower()
        )

        if not self.lista_imagens:
            self.indice_imagem = -1
            self.area_imagem.imagem = None
            self.area_imagem.pontos = []
            self.area_imagem.cantos = []
            self.area_imagem.meios_laterais = []
            self.area_imagem.invalidar_cache_grade()
            self.area_imagem.update()

            self.indicador.setText("Nenhuma imagem encontrada")
            self.botao_anterior.setEnabled(False)
            self.botao_proximo.setEnabled(False)
            return

        self.area_imagem.pontos = []
        self.area_imagem.cantos = []
        self.area_imagem.meios_laterais = []
        self.area_imagem.invalidar_cache_grade()

        self.indice_imagem = 0
        self._tamanho_inicial_definido = False
        self.mostrar_imagem_atual()

    def ajustar_tamanho_para_imagem(self, forcar=False):
        """Define apenas o tamanho inicial; depois a janela fica livre para redimensionar."""
        if self.area_imagem.imagem is None:
            return

        if self._tamanho_inicial_definido and not forcar:
            return

        largura_original = self.area_imagem.imagem.width()
        altura_original = self.area_imagem.imagem.height()

        if largura_original <= 0 or altura_original <= 0:
            return

        proporcao = altura_original / largura_original

        tela = QApplication.primaryScreen()
        area_disponivel = tela.availableGeometry()

        largura = min(1200, max(800, area_disponivel.width() - 80))
        altura = round(largura * proporcao)

        altura_maxima = max(500, area_disponivel.height() - 120)

        if altura > altura_maxima:
            altura = altura_maxima
            largura = round(altura / proporcao)

        # A área da foto NÃO fica mais fixa. A janela pode ser aumentada
        # ou diminuída livremente depois desse tamanho inicial.
        self.area_imagem.setMinimumSize(400, 300)
        self.area_imagem.resize(largura, altura)

        altura_barra = self.botao_pasta.sizeHint().height()
        largura_janela = max(800, largura)
        altura_janela = max(500, altura + altura_barra)

        self.resize(largura_janela, altura_janela)
        self._tamanho_inicial_definido = True
        self.posicionar_botoes_navegacao()

    def mostrar_imagem_atual(self):
        if not self.lista_imagens:
            return

        caminho = self.lista_imagens[self.indice_imagem]

        criar_grade = len(self.area_imagem.pontos) != 6

        self.area_imagem.carregar_imagem(
            caminho,
            criar_grade=False
        )

        self.ajustar_tamanho_para_imagem()

        if criar_grade:
            self.area_imagem.criar_grade_inicial()

        self.area_imagem.update()

        numero_atual = self.indice_imagem + 1
        total = len(self.lista_imagens)
        nome = os.path.basename(caminho)

        self.indicador.setText(
            f"{numero_atual} / {total} — {nome}"
        )

        self.botao_anterior.setEnabled(self.indice_imagem > 0)
        self.botao_proximo.setEnabled(
            self.indice_imagem < total - 1
        )

        self.campo_foto.setText(str(numero_atual))

    def ir_para_foto(self):
        if not self.lista_imagens:
            return

        texto = self.campo_foto.text().strip()
        if not texto:
            return

        try:
            numero = int(texto)
        except ValueError:
            self.campo_foto.selectAll()
            self.campo_foto.setFocus()
            return

        total = len(self.lista_imagens)
        if numero < 1 or numero > total:
            self.campo_foto.selectAll()
            self.campo_foto.setFocus()
            return

        novo_indice = numero - 1
        if novo_indice != self.indice_imagem:
            self.indice_imagem = novo_indice
            self.mostrar_imagem_atual()

    def imagem_anterior(self):
        if self.indice_imagem > 0:
            self.indice_imagem -= 1
            self.mostrar_imagem_atual()

    def imagem_proxima(self):
        if self.indice_imagem < len(self.lista_imagens) - 1:
            self.indice_imagem += 1
            self.mostrar_imagem_atual()

    def alternar_medicao(self, ativada):
        if ativada and self.botao_linha_bordo.isChecked():
            self.botao_linha_bordo.setChecked(False)
            self.botao_linha_bordo.setText("↔ Linha de bordo")
            self.area_imagem.alternar_linha_bordo(False)
        self.botao_medicao.setText("✏️ Parar medição" if ativada else "✏️ Medir")
        self.area_imagem.alternar_medicao(ativada)

    def alternar_desenho(self, ativada):
        """Ativa o modo Desenho na área da imagem sem remover ferramentas antigas."""
        area = self.area_imagem
        if ativada:
            if self.botao_medicao.isChecked():
                self.botao_medicao.setChecked(False)
                self.botao_medicao.setText("✏️ Medir")
            if hasattr(self, "botao_linha_bordo") and self.botao_linha_bordo.isChecked():
                self.botao_linha_bordo.setChecked(False)
                self.botao_linha_bordo.setText("↔ Linha de bordo")
            area.alternar_medicao(False)
            area.alternar_linha_bordo(False)
            self.botao_desenho.setText("☑ Desenho")
        else:
            self.botao_desenho.setText("✏️ Desenho")
        area.alternar_desenho(ativada)

    def excluir_desenho(self):
        """Pede confirmação e remove somente o desenho da foto atual."""
        area = self.area_imagem
        caminho = area.caminho_foto_atual
        if not caminho:
            return

        resposta = QMessageBox.question(
            self,
            "Confirmar exclusão",
            "Tem certeza que deseja excluir?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if resposta != QMessageBox.StandardButton.Yes:
            # "Não": mantém desenho, tabela, etapa e seleção exatamente como estavam.
            return

        area.excluir_desenho_atual()
        area._lado_desenho_temporario = None
        area._ponto_desenho_temporario = None
        area._bordos_desenho_por_faixa = {}
        area.selecoes_temporarias_desenho[caminho] = None
        area.etapas_desenho_por_foto[caminho] = 0
        area.etapa_desenho = 0
        etapas = area._etapas_desenho()
        area.prompt_desenho = etapas[0]["prompt"] if area.modo_desenho and etapas else ""
        area._salvar_estado_desenho()
        area.update()

    def alternar_grade(self, ativada):
        if ativada:
            self.botao_grade.setText("☑ Ocultar grade")
            self.area_imagem.criar_grade_inicial()
        else:
            self.botao_grade.setText("☐ Mostrar grade")

        self.area_imagem.alternar_grade(ativada)

    def closeEvent(self, evento):
        # Garante persistência ao fechar normalmente a janela.
        if hasattr(self, "area_imagem"):
            self.area_imagem._salvar_estado_desenho()
        super().closeEvent(evento)

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        if hasattr(self, "area_imagem"):
            self.posicionar_botoes_navegacao()


app = QApplication(sys.argv)

janela = SistemaRodovia()
janela.show()

sys.exit(app.exec())
