package com.example.speako.domain.presentation.controller;

import com.example.speako.domain.presentation.service.DownloadService;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.InputStreamResource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.ByteArrayInputStream;

@RestController
@RequestMapping("/api/presentations")
@RequiredArgsConstructor
public class DownloadController {

    private final DownloadService downloadService;

    /**
     * 1. 일반 대본 다운로드 (.txt)
     */
    @GetMapping("/{presentationId}/download/script")
    public ResponseEntity<InputStreamResource> downloadScript(
            @PathVariable("presentationId") Long presentationId
    ) {
        ByteArrayInputStream bis = downloadService.generateScriptTextFile(presentationId);

        HttpHeaders headers = new HttpHeaders();
        headers.add("Content-Disposition", "attachment; filename=script.txt");

        return ResponseEntity.ok()
                .headers(headers)
                .contentType(MediaType.TEXT_PLAIN)
                .body(new InputStreamResource(bis));
    }

    /**
     * 2. 하이라이팅 포함 대본 다운로드 (.txt 또는 리포트 형식)
     */
    @GetMapping("/{presentationId}/download/highlighted")
    public ResponseEntity<InputStreamResource> downloadHighlightedScript(
            @PathVariable("presentationId") Long presentationId
    ) {
        ByteArrayInputStream bis = downloadService.generateHighlightedScriptReport(presentationId);

        HttpHeaders headers = new HttpHeaders();
        headers.add("Content-Disposition", "attachment; filename=highlighted_script_report.txt");

        return ResponseEntity.ok()
                .headers(headers)
                .contentType(MediaType.TEXT_PLAIN)
                .body(new InputStreamResource(bis));
    }
}