package com.example.speako.global.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;

@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http

                .csrf(AbstractHttpConfigurer::disable)


                .formLogin(AbstractHttpConfigurer::disable)


                .httpBasic(AbstractHttpConfigurer::disable)


                .authorizeHttpRequests(authorize -> authorize
                        .requestMatchers(
                                "/swagger-ui/**",       // 스웨거 UI 화면
                                "/v3/api-docs/**",      // 스웨거 API 스펙 데이터
                                "/swagger-resources/**",
                                "/webjars/**",
                                "/api/users/signup",    // 회원가입 API
                                "/api/main/**"          // 비회원 메인 페이지 API
                        ).permitAll()

                        // 그 외의 모든 요청은 로그인 필요
                        .anyRequest().authenticated()
                );

        return http.build();
    }
}